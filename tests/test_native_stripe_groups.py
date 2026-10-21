"""Complete native decoration groups survive partial seeds and vehicle bounds."""
import unittest

from components import attach_native_contexts
from tests.test_internal_model_review import scene


class NativeStripeGroupTests(unittest.TestCase):
    def fixture(self):
        objects, candidates, first = scene()
        # A selected narrow line fits the vehicle, while the wider colour in
        # the same original group deliberately extends beyond its shell.
        seed = dict(id='seed', bbox=[10, 10, 90, 14], fill='#005599',
                    source_offset=1500, d='M10 10H90V14H10Z')
        wide = dict(id='wide', bbox=[20, 10, 106, 30], fill='#66ccee',
                    source_offset=1600, d='M20 10H106V30H20Z')
        objects.extend([seed, wide])
        group = dict(native_type='AGroup', member_ids=['seed', 'wide'],
                     members=[seed, wide], bbox=[10, 10, 106, 30],
                     area=1920, aspect=4.8, source_span=[1400, 1700])
        proposal = dict(id='G005', native_type='PaintSurface', member_ids=['seed'],
                        members=[seed], bbox=seed['bbox'], area=320, aspect=20,
                        source_span=[1500, 1500])
        candidates.append(proposal)
        first['categories']['彩条'] = ['G005']
        return objects, candidates, first, group


    def test_smallest_native_owner_is_used_and_whole_seed_is_not_rewrapped(self):
        objects, candidates, first, native = self.fixture()
        larger = dict(native, member_ids=['seed', 'wide', 'lamp'])
        attach_native_contexts(candidates, [larger, native])
        self.assertEqual(candidates[-1]['native_context']['member_ids'], ['seed', 'wide'])
        complete = dict(native)
        attach_native_contexts([complete], [native])
        self.assertNotIn('native_context', complete)


if __name__ == '__main__':
    unittest.main()
