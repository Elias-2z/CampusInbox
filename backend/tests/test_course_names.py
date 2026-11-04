import pytest

from campus_inbox.connectors.courses import course_name


@pytest.mark.parametrize(
    "name,expected",
    [
        ("2026年机械制造基础三班", "机械制造基础"),
        ("软件工程（2026秋）课程群", "软件工程"),
        ("数据库原理三班", "数据库原理"),
        ("【2026级】高等数学A（3班）", "高等数学A"),
        ("大学英语Ⅲ三班", "大学英语Ⅲ"),
        ("大学英语三", "大学英语三"),
        ("数字电路2班", "数字电路"),
        ("软件工程群", "软件工程"),
        ("2026级三班通知群", ""),
        ("班级通知群", ""),
        ("高等数学/软件工程", ""),
        ("2026年", ""),
    ],
)
def test_group_course_cleanup(name, expected):
    assert course_name(name) == expected
