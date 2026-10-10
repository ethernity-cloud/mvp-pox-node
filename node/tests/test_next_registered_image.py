"""utils.next_registered_image: which registered image a mirror attempts in
a round. The newest due registration goes first among those with the fewest
attempts; pinned-and-verified, refused, unverifiable and deferred entries are
not attempted."""
from utils import next_registered_image

NOW = 1_000_000.0


def entry(**fields):
    e = {'pinned': False, 'attempts': 0, 'next_at': 0}
    e.update(fields)
    return e


def test_the_newest_registration_goes_first():
    images = {'old': entry(), 'newer': entry(), 'newest': entry()}
    assert next_registered_image(images, NOW) == 'newest'


def test_fewer_attempts_go_before_a_newer_registration_that_keeps_failing():
    images = {'old': entry(attempts=1), 'newest': entry(attempts=2)}
    assert next_registered_image(images, NOW) == 'old'


def test_a_pinned_and_verified_image_is_not_attempted_again():
    images = {'done': entry(pinned=True, verified_under='bounds'), 'old': entry()}
    assert next_registered_image(images, NOW) == 'old'


def test_a_pinned_image_without_a_verdict_under_the_bounds_is_attempted():
    images = {'pinned': entry(pinned=True)}
    assert next_registered_image(images, NOW) == 'pinned'


def test_refused_unverifiable_and_deferred_entries_are_skipped():
    images = {
        'refused': entry(refused_under='bounds'),
        'unverifiable': entry(unverifiable_under='bounds'),
        'later': entry(next_at=NOW + 1),
    }
    assert next_registered_image(images, NOW) is None
    images['now'] = entry(next_at=NOW)
    assert next_registered_image(images, NOW) == 'now'


def test_nothing_due_is_none():
    assert next_registered_image({}, NOW) is None
