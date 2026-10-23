import unittest

from wechat_autoreply.wechat import Calibration, parse_chat, parse_list


def node(kind, label='', rect=(0, 200, 440, 73), **kw):
    return dict(type=kind, label=label, rect=list(rect), **kw)


def obs(nodes, truncated=False):
    return dict(app='com.tencent.xin', viewport=dict(width=440, height=956),
                nodes=nodes, total_nodes=len(nodes), truncated=truncated)


class WechatTests(unittest.TestCase):
    def setUp(self):
        self.cal = Calibration(list_bottom=868, muted_marker_verified=True,
                               list_region_complete=True,
                               identities={'测试甲': 'id-a', '测试乙': 'id-b'})

    def test_list_preserves_rows_and_muted_status(self):
        rows = parse_list(obs([
            node('NavigationBar', '微信(3)', rect=(0, 56, 440, 45)),
            node('Cell', '测试甲,3条未读,你好,20:45'),
            node('Cell', '测试乙,未读,你好,20:30,消息免打扰', rect=(0, 273, 440, 73)),
        ]), self.cal)
        self.assertEqual([r.key for r in rows], ['id-a', 'id-b'])
        self.assertEqual([r.muted for r in rows], [False, True])
        self.assertTrue(rows[0].unread)

    def test_unknown_marker_capability_cannot_mean_unmuted(self):
        cal = Calibration(list_bottom=868, identities={'测试甲': 'id-a'})
        rows = parse_list(obs([node('NavigationBar', '微信'), node('Cell', '测试甲,1条未读,你好,20:45')]), cal)
        self.assertIsNone(rows[0].muted)

    def test_truncated_tree_is_not_complete(self):
        with self.assertRaisesRegex(ValueError, '截断'):
            parse_list(obs([node('Cell', '测试甲,未读,你好,20:45')], True), self.cal)

    def test_same_name_and_partially_hidden_rows_are_not_verified(self):
        rows = parse_list(obs([
            node('NavigationBar', '微信'),
            node('Cell', '测试甲,未读,甲,20:45'),
            node('Cell', '测试甲,未读,乙,20:40', rect=(0, 273, 440, 73)),
            node('Cell', '测试乙,未读,丙,20:30', rect=(0, 848, 440, 73)),
        ]), self.cal)
        self.assertEqual(len(rows), 2)
        self.assertFalse(any(r.identity_verified for r in rows))

    def test_group_and_service_rows_are_not_single_contacts(self):
        rows = parse_list(obs([node('NavigationBar', '微信'),
            node('Cell', '公众号,未读,[3条]预览,20:30'),
            node('Cell', '测试群,未读,某人: 你好,20:30')]), self.cal)
        self.assertFalse(any(r.identity_verified for r in rows))

    def test_chat_directions_and_nontext_are_preserved(self):
        view = obs([node('NavigationBar', '测试甲', rect=(0, 56, 440, 45)),
                    node('Button', '返回,3', rect=(16, 56, 40, 45)),
                    node('Other', '测试甲,你好', rect=(0, 200, 440, 58)),
                    node('Other', '我,你好', rect=(0, 258, 440, 58)),
                    node('Other', '测试甲,图片,', rect=(0, 316, 440, 120)),
                    node('Other', '垂直滚动条, 2页', rect=(407, 100, 30, 600), value='100%'),
                    node('TextView', '', rect=(50, 800, 280, 40), value='')])
        snap = parse_chat(view, self.cal, 'id-a', muted=False)
        self.assertEqual([m.direction for m in snap.messages], ['incoming', 'outgoing', 'incoming'])
        self.assertEqual(snap.messages[-1].kind, 'image')
        self.assertTrue(snap.at_latest)
        self.assertTrue(snap.identity_verified)

    def test_explicit_send_failure_control_is_preserved(self):
        view = obs([node('NavigationBar','测试甲'),node('Other','我,收到'),
                    node('Button','发送失败，双击重新发送'),
                    node('Other','垂直滚动条, 1页',value='100%')])
        snap = parse_chat(view,self.cal,'id-a',muted=False)
        self.assertTrue(getattr(snap,'send_failed',False))


if __name__ == '__main__':
    unittest.main()
