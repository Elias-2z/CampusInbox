"""Retain exact instruction clauses within one already-recognized task block."""
import re
import unicodedata

_META=re.compile(r'^(?:课程(?:名称)?|任务名称|作业名称|考试名称|发布时间|开始时间|结束时间|截止(?:时间|日期)?|截至|最晚|提交时间|提交方式|来源|发件人)\s*[:：]')
_DIRECTIVE=re.compile(r'^(?:请|需要|需|须|必须|务必|应当|要求|只使用|仅使用|使用|采用|用(?=[A-Za-z]|[^。；;，,\n]{1,18}(?:实现|编写|完成|计算|求解|写))|编写|实现|输出|附上|附带|附加|附(?=截图|代码|文件|报告)|提供|携带|带上|不得|禁止|不能|不要|不少于|不超过|至少|至多|字数|字体|格式|命名|文件名|(?:报告|正文|文档|论文|代码|文件)(?:需|应|必须|不少于|不超过|至少|至多|格式|命名))')
_LIST_PREFIX=re.compile(r'^\s*(?:[-*•●]\s+|\d{1,2}[.、)）]\s*|[（(](?:\d{1,2}|[一二三四五六七八九十]+)[)）]\s*|[一二三四五六七八九十]+、\s*)')
_ACTION=re.compile(r'^(?:独立完成|完成|分析|比较|设计|绘制|计算|验证|测试|解释|说明|讨论|总结|给出|列出|记录|证明|推导)(?!了|过|\s*[:：])')


def _key(text):
    text=unicodedata.normalize('NFKC',text)
    text=re.sub(r'^(?:(?:请|需要|需|同时|另外|一并|提交|上传|上交|附加|附带|附上|附)\s*)+','',text)
    return re.sub(r'\s+','',text).strip('：:。；;')


def enrich(block,title,course,known):
    requirements=list(dict.fromkeys(known));labelled=False
    for line in block.splitlines():
        line=_LIST_PREFIX.sub('',line.strip(),count=1)
        if _META.match(line):continue
        line=re.sub(r'^《[^》]+》\s*','',line)
        if course:line=re.sub(r'^'+re.escape(course)+r'\s*[:：]\s*','',line)
        # A numeric task's full title can itself contain the instruction.
        numeric=re.match(r'^(?:作业|任务|实验)\s*\d+\s*[:：]\s*(.+)$',line)
        if numeric:line=numeric[1]
        elif title and line.startswith(title):line=line[len(title):].lstrip(' \t：:，,')
        heading=re.match(r'^(?:任务要求|作业要求|具体要求|要求)\s*[:：]\s*(.*)$',line)
        if heading:labelled=True;line=heading[1]
        for clause in re.split(r'[。；;，,]',line):
            clause=clause.strip()
            if not clause or _META.match(clause):continue
            if re.fullmatch(r'请(?:尽快|及时)(?:作答|完成|提交|查看)|请查看结果',clause):continue
            action=re.sub(r'^(?:并且|并|然后|同时|另外)\s*','',clause,count=1)
            if not labelled and not (_DIRECTIVE.match(action) or _ACTION.match(action)):continue
            if re.match(r'^(?:截止|截至|延期|延至|推迟|其他要求不变|其余要求不变)',clause):continue
            if any(_key(clause)==_key(item) for item in requirements):continue
            material=re.match(r'^(?:请|需要|需|务必)?(?:同时|一并|另外)?(?:提交|上传|上交)\s*(.+)$',clause)
            if material:
                parts=[p.strip() for p in re.split(r'[、和及+＋]',material[1]) if p.strip()]
                if parts and all(any(_key(part)==_key(item) for item in requirements) for part in parts):continue
            requirements.extend(clause[i:i+180] for i in range(0,len(clause),180))
    return requirements[:30],len(requirements)>30
