"""Regression cases for real parser defects and conservative motif transfer."""
import tempfile
import unittest
import zlib
import struct
from pathlib import Path

from core.fs_geometry import path_from_nodes
from core.fs_container import unpack_objects
from reference_match import match_motifs,path_features


class ReferenceMatching(unittest.TestCase):
    def test_stale_handles_do_not_turn_a_line_into_cubic(self):
        nodes=[(0,0.,0.,0.,0.,8.,7.),(0,10.,0.,-5.,9.,0.,0.)]
        d,_=path_from_nodes(nodes,False)
        self.assertEqual(d,'M0,0 L10,0')

    def test_flagged_cubic_retains_zero_control_handles(self):
        nodes=[(0,0.,0.,0.,0.,0.,0.),(1,10.,0.,0.,0.,0.,0.)]
        d,_=path_from_nodes(nodes,False)
        self.assertIn('C0,0 10,0 10,0',d)

    def test_padded_final_zlib_block_uses_declared_valid_length(self):
        compressed=zlib.compress(b'abc'+b'\0'*5)
        stream=b'\x03\x02'+struct.pack('<III',len(compressed),3,8)+struct.pack('<I',len(compressed))+compressed
        raw,_=unpack_objects(stream)
        self.assertEqual(raw,b'abc')

    def make_reference(self,directory):
        p=Path(directory)/'two-parts.svg'
        p.write_text('<svg xmlns="http://www.w3.org/2000/svg"><g transform="scale(1,-1)">'
                     '<path d="M0 0 C2 3 7 3 10 0 L10 -1 L0 -1 Z"/>'
                     '<path d="M2 4 C3 8 7 8 8 4 L8 3 L2 3 Z"/></g></svg>',encoding='utf8')

    def objects(self,wrong_layout=False):
        # Both shapes undergo x*2+100, y*3+50, including control points.
        ds=['M100 50 C104 59 114 59 120 50 L120 47 L100 47 Z',
            'M104 62 C106 74 114 74 116 62 L116 59 L104 59 Z']
        if wrong_layout:ds[1]='M104 162 C106 174 114 174 116 162 L116 159 L104 159 Z'
        return [{'id':'unrelated-id-'+str(i),'d':d,'bbox':path_features(d)['bbox'],'fill':'#0000ff','source_offset':i} for i,d in enumerate(ds)]

    def test_full_pattern_matches_across_file_and_scale_without_id_hints(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_reference(tmp)
            result=match_motifs(self.objects(),tmp,'f'*64)
            self.assertEqual(set(result['selected_ids']),{'unrelated-id-0','unrelated-id-1'})
            self.assertFalse(result['in_sample'])
            self.assertEqual(result['coordinate_transform'],[2.,3.,100.,50.])

    def test_matching_shapes_with_wrong_relative_layout_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_reference(tmp)
            self.assertEqual(match_motifs(self.objects(True),tmp)['selected_ids'],[])

    def test_partial_motif_is_not_auto_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_reference(tmp)
            result=match_motifs(self.objects()[:1],tmp)
            self.assertEqual(result['selected_ids'],[])
            self.assertTrue(result['candidate_ids'])


if __name__=='__main__':unittest.main()
