"""Conservative group-name cleanup; course levels such as A, III and 三 stay intact."""

import re

META = (
    r"(?:20\d{2}(?:[-/—]20?\d{2,4})?(?:学年|年度|年|级|届)?(?:[春秋](?:季|学期)?)?|"
    r"[一二三四五六七八九十百0-9]+(?:教学班|班)|[春秋](?:季|学期))"
)


def course_name(group_name: str) -> str:
    text = group_name.strip()
    text = re.sub(r"[（(【\[]\s*" + META + r"\s*[）)】\]]", "", text)
    text = re.sub(
        r"^\s*20\d{2}(?:[-/—]20?\d{2,4})?(?:学年|年度|年|级|届)?(?:[春秋](?:季|学期)?)?[\s_·—-]*",
        "",
        text,
    )
    for _ in range(3):
        text = re.sub(r"(?:课程群|教学群|学习群|交流群|通知群|作业群|答疑群|群)$", "", text.strip())
        text = re.sub(r"(?:第)?[一二三四五六七八九十百0-9]+(?:教学班|班)$", "", text.strip())
        text = re.sub(r"(?:20\d{2}年)?[春秋](?:季|学期)$", "", text.strip())
        text = text.strip(" _-—·()（）【】[]")
    if (
        len(text) < 2
        or len(text) > 100
        or re.search(r"[/、]|以及|和.*课", text)
        or text
        in {"通知", "班级", "课程", "教学", "学习", "班群", "班级群", "交流", "综合", "通知群"}
        or not re.search(r"[\u4e00-\u9fffA-Za-z]", text)
    ):
        return ""
    return text
