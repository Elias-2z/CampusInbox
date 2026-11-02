"""Normalize explicit Chinese date/time numerals for parsing, retaining source spans."""
import re

_DIGITS={c:str(i) for i,c in enumerate('零一二三四五六七八九')}
_DIGITS.update({'〇':'0','两':'2'})
_N=r'[零〇一二两三四五六七八九十\d]'
_DATE=re.compile(r'(?:(?P<year>[零〇一二三四五六七八九\d]{4})\s*年\s*)?(?P<month>'+_N+r'{1,3})\s*月\s*(?P<day>'+_N+r'{1,3})\s*[日号]')
_CLOCK=re.compile(r'(?P<hour>[零〇一二两三四五六七八九十]{1,3})\s*[点时](?:(?P<minute>[零〇一二两三四五六七八九十]{1,3})\s*分)?')


def number(value):
    if '十' not in value:return int(''.join(_DIGITS.get(c,c) for c in value))
    left,right=value.split('十')
    return int(_DIGITS.get(left,left) or '1')*10+int(_DIGITS.get(right,right) or '0')


def normalize(text):
    replacements=[]
    for m in _DATE.finditer(text):
        if not re.search(r'[零〇一二两三四五六七八九十]',m[0]):continue
        try:value=(str(number(m['year']))+'年' if m['year'] else '')+str(number(m['month']))+'月'+str(number(m['day']))+m[0].rstrip()[-1]
        except ValueError:continue
        replacements.append((m.start(),m.end(),value))
    for m in _CLOCK.finditer(text):
        try:value=str(number(m['hour']))+'点'+(str(number(m['minute']))+'分' if m['minute'] else '')
        except ValueError:continue
        replacements.append((m.start(),m.end(),value))
    parts=[];positions=[];cursor=0
    for start,end,value in sorted(replacements):
        parts.append(text[cursor:start]);positions.extend(range(cursor,start))
        parts.append(value);positions.extend(start+min(end-start-1,i*(end-start)//len(value)) for i in range(len(value)))
        cursor=end
    parts.append(text[cursor:]);positions.extend(range(cursor,len(text)));positions.append(len(text))
    return ''.join(parts),positions


def original_span(original,normalized,positions,fragment):
    start=normalized.find(fragment)
    return original[positions[start]:positions[start+len(fragment)]] if fragment and start>=0 else fragment


def has_explicit_year(text):
    normalized,_=normalize(text)
    return bool(re.search(r'(?<!\d)\d{4}\s*[年/-]\s*\d{1,2}',normalized))
