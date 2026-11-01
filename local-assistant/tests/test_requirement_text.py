import unittest
from courseinbox.core import parse_message


class RequirementTextTests(unittest.TestCase):
    def parse(self,text):return parse_message(text,'2026-10-29T10:36:00+08:00','软件工程')

    def test_implementation_and_prohibition_constraints_are_not_lost(self):
        cases=[
            ('实验一：需使用C++语言编写程序。','需使用C++语言编写程序'),
            ('第一次作业：使用Python完成统计，不能调用现成接口。','不能调用现成接口'),
            ('实验二：附上运行截图。','附上运行截图'),
            ('第一次作业：报告不少于1500字。','报告不少于1500字'),
            ('第一次作业：文件命名为学号_姓名。','文件命名为学号_姓名'),
            ('第一次作业\n任务要求：C++代码和测试截图。','C++代码和测试截图'),
            ('课程考试\n请携带准考证。','请携带准考证'),
        ]
        for text,expected in cases:
            with self.subTest(text=text):self.assertTrue(any(expected in r for r in self.parse(text)[0]['requirements']))

    def test_material_and_constraint_are_both_retained(self):
        items=self.parse('实验一：请提交报告。需使用C++编写程序，不得使用第三方库。')[0]['requirements']
        self.assertIn('报告',items);self.assertIn('需使用C++编写程序',items);self.assertIn('不得使用第三方库',items)

    def test_numbered_and_bulleted_instructions_are_retained(self):
        text='实验一\n1. 使用Python实现排序\n（2）分析时间复杂度\n三、给出测试结果\n- 不得使用第三方库'
        items=self.parse(text)[0]['requirements']
        for expected in ('使用Python实现排序','分析时间复杂度','给出测试结果','不得使用第三方库'):
            self.assertIn(expected,items)

    def test_coordinated_instructions_keep_source_wording(self):
        items=self.parse('实验一：使用Python实现排序，分析时间复杂度，并给出测试结果。')[0]['requirements']
        self.assertIn('分析时间复杂度',items)
        self.assertIn('并给出测试结果',items)

    def test_list_metadata_is_not_a_material_or_requirement(self):
        items=self.parse('实验一\n任务要求：\n- 独立完成\n- 截止时间：10月31日\n- 提交方式：学习通上传')[0]
        self.assertEqual(items['requirements'],['独立完成'])
        self.assertEqual(items['submission_method'],'学习通上传')

    def test_new_action_words_do_not_promote_narrative(self):
        items=self.parse('实验一说明：研究团队分析时间复杂度，并给出了测试结果。')[0]['requirements']
        self.assertEqual(items,[])

    def test_list_requirements_survive_confirmation_without_cross_task_leakage(self):
        import tempfile
        from pathlib import Path
        from courseinbox.core import Inbox
        with tempfile.TemporaryDirectory() as directory:
            box=Inbox(Path(directory)/'inbox.sqlite3')
            result=box.ingest('实验一：\n1. 分析时间复杂度\n2. 给出测试结果\n实验二：\n- 使用Python绘图\n- 不得使用第三方库',captured_at='2026-10-29T10:36:00+08:00',course='软件工程')
            self.assertEqual(len(result['proposals']),2)
            self.assertEqual(box.tasks(),[])
            for p in result['proposals']:box.confirm(p['id'])
            tasks={t['title']:t for t in box.tasks()}
            self.assertEqual([r['text'] for r in tasks['实验一']['checklist']],['分析时间复杂度','给出测试结果'])
            self.assertEqual([r['text'] for r in tasks['实验二']['checklist']],['使用Python绘图','不得使用第三方库'])

    def test_two_tasks_do_not_borrow_each_others_requirements(self):
        items=self.parse('实验一：需使用C++实现排序。实验二：需使用Python绘图。')
        self.assertEqual(len(items),2)
        self.assertIn('需使用C++实现排序',items[0]['requirements'])
        self.assertFalse(any('Python' in r for r in items[0]['requirements']))
        self.assertIn('需使用Python绘图',items[1]['requirements'])
        self.assertFalse(any('C++' in r for r in items[1]['requirements']))

    def test_title_metadata_and_deadline_are_not_new_requirements(self):
        items=self.parse('第一次作业\n发布时间：2026年10月29日\n截止时间：2026年10月31日')
        self.assertEqual(items[0]['requirements'],[])

    def test_narrative_is_not_turned_into_an_instruction(self):
        items=self.parse('实验一说明：研究团队使用C++实现了排序。')
        self.assertFalse(any('研究团队' in r for r in items[0]['requirements']))

    def test_model_omission_cannot_remove_rule_requirements(self):
        from courseinbox.agent import Agent
        text='实验一：需使用C++编写程序，不得使用第三方库。'
        rules=self.parse(text)
        merged,_,_,_=Agent._merge_analysis_candidates(rules,[dict(rules[0],requirements=[])],text)
        self.assertIn('需使用C++编写程序',merged[0]['requirements'])
        self.assertIn('不得使用第三方库',merged[0]['requirements'])

    def test_overflow_is_explicit_and_each_retained_clause_has_source_evidence(self):
        text='实验一\n'+'\n'.join('必须记录第'+str(i)+'组测试结果' for i in range(40))
        candidate=self.parse(text)[0]
        self.assertEqual(len(candidate['requirements']),30)
        self.assertTrue(any('前30项' in warning for warning in candidate['warnings']))
        self.assertTrue(all(item in text for item in candidate['requirements']))
