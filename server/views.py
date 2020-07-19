import re
import time
from datetime import datetime

from flask import jsonify, render_template, request

from server import app, auth, database, reloader
from server.models import FlagStatus


@app.template_filter('timestamp_to_datetime')
def timestamp_to_datetime(s):
    return datetime.fromtimestamp(s)


@app.route('/')
@auth.auth_required
def index():
    distinct_values = {}
    for column in ['sploit', 'status', 'team']:
        rows = database.query('SELECT DISTINCT {} FROM flags ORDER BY {}'.format(column, column))
        distinct_values[column] = [item[column] for item in rows]

    statuses = [name for name, _ in FlagStatus.__members__.items()]

    # Setup counts and it's 'Total' dictionary
    counts = {'Total': {'TOTAL': 0}}
    for status in statuses:
        counts['Total'][status] = {'count': 0, 'percent': 0}

    # Get the number of statuses for each service and calculate running totals
    max_sploit_total = 0
    for sploit in distinct_values['sploit']:
        counts[sploit] = {'TOTAL': 0}
        for status in statuses:
            count = database.query("SELECT COUNT(*) FROM flags WHERE sploit = '%s' AND status = '%s'" % (sploit, status))[0][0]
            counts[sploit][status] = {'count': count}
            counts[sploit]['TOTAL'] += count
            counts['Total'][status]['count'] += count
            counts['Total']['TOTAL'] += count
        max_sploit_total = max(max_sploit_total, counts[sploit]['TOTAL'])

    # Calculate sploit status percentages based off max_sploit_total
    for sploit in distinct_values['sploit']:
        for sploit_status in [counts[sploit][status] for status in statuses]:
            sploit_status['percent'] = 100 * sploit_status['count'] / max_sploit_total

    # Calculate overall status percentages based off total flag count
    for status_total in [counts['Total'][status] for status in statuses]:
        status_total['percent'] = 100 * status_total['count'] / counts['Total']['TOTAL']

    # Sort by sploit flag total
    counts = {key: value for key, value in sorted(counts.items(), key=lambda item: -item[1]['TOTAL'])}
    config = reloader.get_config()

    server_tz_name = time.strftime('%Z')
    if server_tz_name.startswith('+'):
        server_tz_name = 'UTC' + server_tz_name

    return render_template('index.html',
                           flag_format=config['FLAG_FORMAT'],
                           distinct_values=distinct_values,
                           counts=counts,
                           server_tz_name=server_tz_name)


FORM_DATETIME_FORMAT = '%Y-%m-%d %H:%M'
FLAGS_PER_PAGE = 30


@app.route('/ui/show_flags', methods=['POST'])
@auth.auth_required
def show_flags():
    conditions = []
    for column in ['sploit', 'status', 'team']:
        value = request.form[column]
        if value:
            conditions.append(('{} = ?'.format(column), value))
    for column in ['flag', 'checksystem_response']:
        value = request.form[column]
        if value:
            conditions.append(('INSTR(LOWER({}), ?)'.format(column), value.lower()))
    for param in ['time-since', 'time-until']:
        value = request.form[param].strip()
        if value:
            timestamp = round(datetime.strptime(value, FORM_DATETIME_FORMAT).timestamp())
            sign = '>=' if param == 'time-since' else '<='
            conditions.append(('time {} ?'.format(sign), timestamp))
    page_number = int(request.form['page-number'])
    if page_number < 1:
        raise ValueError('Invalid page-number')

    if conditions:
        chunks, values = list(zip(*conditions))
        conditions_sql = 'WHERE ' + ' AND '.join(chunks)
        conditions_args = list(values)
    else:
        conditions_sql = ''
        conditions_args = []

    sql = 'SELECT * FROM flags ' + conditions_sql + ' ORDER BY time DESC LIMIT ? OFFSET ?'
    args = conditions_args + [FLAGS_PER_PAGE, FLAGS_PER_PAGE * (page_number - 1)]
    flags = database.query(sql, args)

    sql = 'SELECT COUNT(*) FROM flags ' + conditions_sql
    args = conditions_args
    total_count = database.query(sql, args)[0][0]

    return jsonify({
        'rows': [dict(item) for item in flags],

        'rows_per_page': FLAGS_PER_PAGE,
        'total_count': total_count,
    })


@app.route('/ui/post_flags_manual', methods=['POST'])
@auth.auth_required
def post_flags_manual():
    config = reloader.get_config()
    flags = re.findall(config['FLAG_FORMAT'], request.form['text'])

    cur_time = round(time.time())
    rows = [(item, 'Manual', '*', cur_time, FlagStatus.QUEUED.name)
            for item in flags]

    db = database.get()
    db.executemany("INSERT OR IGNORE INTO flags (flag, sploit, team, time, status) "
                   "VALUES (?, ?, ?, ?, ?)", rows)
    db.commit()

    return ''
