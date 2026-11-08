"""Strict, bounded literal data for BB's supported rich-content code fence."""
from __future__ import annotations
import json
import math
import re
from html import unescape

MAX_SOURCE = 32000


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _text(value, limit=160):
    return isinstance(value, str) and 0 < len(value.strip()) <= limit and not any(ord(c) < 32 for c in value)


def _number(value):
    return type(value) in (int, float) and abs(value) <= 1e12 and math.isfinite(value)


def native_chart(value):
    allowed = {'chartType','title','xKey','nameKey','valueKey','series','data',
               'xAxisLabel','axisLabel','valuePrefix','valueSuffix','bounds','min','max','yMin','yMax'}
    if set(value)-allowed or value.get('chartType') not in ('bar','line','pie'):
        raise ValueError('Unsupported native chart fields')
    rows, series = value.get('data'), value.get('series')
    if not isinstance(rows,list) or not 1<=len(rows)<=24 or not all(isinstance(r,dict) for r in rows):
        raise ValueError('Native chart needs literal data rows')
    kind=value['chartType']
    if kind=='pie':
        name_key=value.get('nameKey')
        if not _text(name_key,80) or 'xKey' in value:
            raise ValueError('Pie requires unambiguous nameKey')
        if series is not None and (not isinstance(series,list) or len(series)!=1):
            raise ValueError('Pie requires one series')
        row=series[0] if series else None
        if row is not None and (not isinstance(row,dict) or set(row)-{'dataKey','label'} or not _text(row.get('dataKey'),80)):
            raise ValueError('Invalid pie series')
        data_key=value['valueKey'] if 'valueKey' in value else (row.get('dataKey') if row else None)
        if not _text(data_key,80) or row and row['dataKey']!=data_key:
            raise ValueError('Ambiguous pie valueKey/dataKey')
        selectors=[{'dataKey':data_key,'label':row.get('label',data_key) if row else data_key}]
        x_key=name_key
    else:
        x_key=value.get('xKey')
        if not _text(x_key,80) or 'nameKey' in value or 'valueKey' in value:
            raise ValueError('Bar/line require one xKey')
        if not isinstance(series,list) or not 1<=len(series)<=6:
            raise ValueError('Native chart requires explicit series')
        selectors=series
    for row in selectors:
        if not isinstance(row,dict) or set(row)-{'dataKey','label'} or not _text(row.get('dataKey'),80) or not _text(row.get('label',row.get('dataKey')),80):
            raise ValueError('Invalid native series selector')
    if len({r['dataKey'] for r in selectors})!=len(selectors) or len({r.get('label',r['dataKey']) for r in selectors})!=len(selectors):
        raise ValueError('Ambiguous duplicate native series')
    x_values=[r.get(x_key) for r in rows]
    numeric_x=all(_number(x) for x in x_values)
    if not numeric_x and not all(_text(x,80) for x in x_values):
        raise ValueError('xKey/nameKey values must be uniform literal labels or numbers')
    if kind=='line' and numeric_x and any(a>=b for a,b in zip(x_values,x_values[1:])):
        raise ValueError('Numeric line x values must strictly increase')
    output={'type':kind,'labels':[str(x) for x in x_values],
            'series':[{'name':r.get('label',r['dataKey']),'values':[data.get(r['dataKey']) for data in rows]} for r in selectors]}
    if kind=='line' and numeric_x:
        output['xValues']=x_values
    for key in ('title','xAxisLabel','axisLabel','valuePrefix','valueSuffix'):
        if key in value:
            output[key]=value[key]
    bounds=value.get('bounds',{})
    if not isinstance(bounds,dict) or set(bounds)-{'min','max'}:
        raise ValueError('Unsupported axis bounds')
    bounds=dict(bounds)
    for target, aliases in [('min',('min','yMin')),('max',('max','yMax'))]:
        present=[name for name in aliases if name in value]
        if len(present)>1 or present and target in bounds:
            raise ValueError('Ambiguous bounds')
        if present:
            bounds[target]=value[present[0]]
    if bounds:
        output['bounds']=bounds
    return output


def normalize_chart(value):
    """Accepted schemas are explicit; no data field is guessed or computed."""
    if isinstance(value,dict) and 'chartType' in value:
        value=native_chart(value)
    if not isinstance(value, dict) or value.get('type') not in ('bar', 'line', 'pie'):
        raise ValueError('Unsupported chart type')
    if set(value) - {'type', 'title', 'unit', 'labels', 'series', 'data', 'xAxisLabel', 'axisLabel', 'valuePrefix', 'valueSuffix', 'bounds', 'xValues'}:
        raise ValueError('Unsupported chart fields')
    if 'data' in value:
        data = value['data']
        if set(value) & {'labels', 'series'} or not isinstance(data, dict) or set(data) != {'labels', 'datasets'}:
            raise ValueError('Unsupported chart data')
        if not isinstance(data['datasets'], list):
            raise ValueError('Invalid datasets')
        series = []
        for row in data['datasets']:
            if not isinstance(row, dict) or set(row) != {'label', 'data'}:
                raise ValueError('Dataset needs literal label and data')
            series.append({'name': row['label'], 'values': row['data']})
        labels = data['labels']
    else:
        labels, series = value.get('labels'), value.get('series')
    if not isinstance(labels, list) or not 1 <= len(labels) <= 24 or not all(_text(x, 80) for x in labels):
        raise ValueError('Invalid labels')
    if not isinstance(series, list) or not 1 <= len(series) <= 6:
        raise ValueError('Invalid series')
    for row in series:
        if not isinstance(row, dict) or set(row) != {'name', 'values'} or not _text(row['name'], 80):
            raise ValueError('Invalid series name')
        numbers = row['values']
        if not isinstance(numbers, list) or len(numbers) != len(labels) or not all(
                _number(n) for n in numbers):
            raise ValueError('Series values must be finite aligned numbers')
    if value['type'] == 'pie' and (len(series) != 1 or min(series[0]['values']) < 0 or sum(series[0]['values']) <= 0):
        raise ValueError('Pie requires one nonnegative, nonempty total')
    for key, limit in [('title', 160), ('unit', 40)]:
        if key in value and not _text(value[key], limit):
            raise ValueError('Invalid ' + key)
    for key in ('xAxisLabel','axisLabel','valuePrefix','valueSuffix'):
        if key in value and (not isinstance(value[key],str) or len(value[key])>160 or any(ord(c)<32 for c in value[key])):
            raise ValueError('Invalid axis/format label')
    if 'xValues' in value:
        x=value['xValues']
        if value['type']!='line' or not isinstance(x,list) or len(x)!=len(labels) or not all(_number(n) for n in x) or any(a>=b for a,b in zip(x,x[1:])):
            raise ValueError('Invalid numeric x values')
    if 'bounds' in value:
        bounds=value['bounds']
        numbers=[n for row in series for n in row['values']]
        if value['type']=='pie' or not isinstance(bounds,dict) or not bounds or set(bounds)-{'min','max'} or not all(_number(n) for n in bounds.values()):
            raise ValueError('Invalid bounds')
        if 'min' in bounds and bounds['min']>min(numbers) or 'max' in bounds and bounds['max']<max(numbers) or 'min' in bounds and 'max' in bounds and bounds['min']>=bounds['max']:
            raise ValueError('Bounds would conceal source values')
    return {'kind': 'chart', 'type': value['type'], 'labels': labels, 'series': series,
            **{key: value[key] for key in ('title', 'unit', 'xAxisLabel', 'axisLabel', 'valuePrefix', 'valueSuffix', 'bounds', 'xValues') if key in value}}


def literal_json(payload):
    if len(payload)>MAX_SOURCE:
        raise ValueError('Literal too large')
    return json.loads(payload, object_pairs_hook=_unique,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def literal_content(attributes):
    """Parse only JSON in a quoted attribute or one JSX expression wrapper."""
    match = re.search(r'(?<![-\w])content\s*=\s*', attributes)
    if not match:
        raise ValueError('No literal content')
    raw = attributes[match.end():].lstrip()
    if len(raw) > MAX_SOURCE or not raw:
        raise ValueError('Chart content too large')
    if raw[0] in ('"', "'"):
        quote = raw[0]
        end = raw.find(quote, 1)
        if end < 0:
            raise ValueError('Unclosed content')
        payload = unescape(raw[1:end])
    elif raw[0] == '{':
        end = balanced_end(raw, 0)
        payload = raw[1:end - 1].strip()
    else:
        raise ValueError('Only literal JSON is supported')
    return literal_json(payload)


def balanced_end(source, start):
    depth, quote, escaped = 0, None, False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == '{':
            depth += 1
            if depth > 20:
                raise ValueError('Nesting limit')
        elif char == '}':
            depth -= 1
            if depth == 0:
                return index + 1
    raise ValueError('Unclosed expression')


def component_spans(source, name):
    """Find component boundaries while respecting quoted data and braces."""
    pattern = re.compile('<' + name + r'\b')
    offset = 0
    while match := pattern.search(source, offset):
        index, quote = match.end(), None
        while index < len(source):
            char = source[index]
            if quote:
                if char == quote:
                    quote = None
                elif char == '\\':
                    index += 1
            elif char in ('"', "'"):
                quote = char
            elif char == '{':
                try:
                    index = balanced_end(source, index)
                    continue
                except ValueError:
                    break
            elif char == '>':
                end = index + 1
                if source[index - 1] != '/':
                    close = source.find('</' + name + '>', end)
                    if close < 0:
                        break
                    end = close + len(name) + 3
                yield match.start(), end, source[match.end():index].rstrip('/')
                offset = end
                break
            index += 1
        else:
            return
        if offset <= match.start():
            return


def rich_fence(value):
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    # Payload strings may contain fence delimiters; avoid accidentally closing it.
    runs = re.findall(r'`{3,}', payload)
    fence = '`' * max([3] + [len(run) + 1 for run in runs])
    return '\n\n' + fence + 'chatgpt-rich\n' + payload + '\n' + fence + '\n\n'


def replace_literal_charts(fragment, fallback):
    result, end = [], 0
    for start, stop, attributes in component_spans(fragment, 'Chart'):
        result.append(fragment[end:start])
        try:
            result.append(rich_fence(normalize_chart(literal_content(attributes))))
        except (ValueError, TypeError, RecursionError):
            result.append(fallback(fragment[start:stop]))
        end = stop
    result.append(fragment[end:])
    return ''.join(result)
