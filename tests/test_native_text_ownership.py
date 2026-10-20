"""Multiline converted lettering and coincident independent badge versions."""
import unittest

from components import emblem_candidates, text_block_candidates


def group(kind, members):
    box = [min(o['bbox'][0] for o in members), min(o['bbox'][1] for o in members),
           max(o['bbox'][2] for o in members), max(o['bbox'][3] for o in members)]
    w, h = box[2]-box[0], box[3]-box[1]
    return dict(native_type=kind, members=members, member_ids=[o['id'] for o in members],
                bbox=box, area=w*h, aspect=w/h, source_span=[0, 1000])


def glyph(oid, box):
    return dict(id=oid, source_offset=int(oid), bbox=box, fill='#000000', d='M0 0H1V1Z')


class NativeTextOwnershipTests(unittest.TestCase):
    def test_both_reading_orders_keep_caption_paths_in_native_owner(self):
        native, expected = [], []
        for index, caption_kind in enumerate(('AText', 'AGroup')):
            dx = index*200
            title = [glyph(str(index*100+i), [dx+i*10, 10, dx+i*10+9, 20]) for i in range(6)]
            caption = [glyph(str(index*100+10+i), [dx+i*2, 4, dx+i*2+1.5, 8]) for i in range(30)]
            native += [group('AText', title), group(caption_kind, caption), group('AGroup', title+caption)]
            expected.append(set(o['id'] for o in title+caption))
        # An equally close caption without shared ownership cannot be joined.
        native.append(group('AText', [glyph(str(500+i), [i*2, 0, i*2+1.5, 3]) for i in range(30)]))
        blocks = text_block_candidates(native)
        self.assertEqual([set(c['member_ids']) for c in blocks], expected)
        objects = {o['id']: o for c in native for o in c['members']}
        # Candidate priority is separately covered by the real-source audit;
        # ensure these proposals contain original glyph objects, not copies.
        self.assertTrue(all(o is objects[o['id']] for c in blocks for o in c['members']))

    def test_smallest_badge_owner_excludes_overlapping_other_company_text(self):
        arc = [glyph('1', [0, 5, 2, 15]), glyph('2', [18, 5, 20, 15])]
        branch = [glyph(str(10+i), [5+i*2, 4, 6+i*2, 6]) for i in range(5)]
        old_arc = [glyph('30', [0, 5, 2, 14]), glyph('31', [18, 5, 20, 14])]
        old_text = [glyph(str(40+i), [4+i*2, 7, 5+i*2, 9]) for i in range(4)]
        native = [group('AArcText', arc), group('AText', branch), group('AGroup', arc+branch),
                  group('AArcText', old_arc), group('AText', old_text), group('AGroup', old_text)]
        # Glyph subgroups in either old text must never become an embedded logo.
        native += [group('AComposite', old_arc), group('AComposite', old_text[:2])]
        badges = emblem_candidates(native)
        desired = next(c for c in badges if set(c['arc_member_ids']) == {'1', '2'})
        self.assertEqual(set(desired['member_ids']), {'1', '2', '10', '11', '12', '13', '14'})
        self.assertEqual(desired['logo_member_ids'], [])
        self.assertFalse(any(c['native_type'] == 'EmblemLogo' for c in badges))


if __name__ == '__main__':
    unittest.main()
