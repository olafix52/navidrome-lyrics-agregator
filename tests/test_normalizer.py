"""Unit tests for metadata normalization, duration tolerance, and string similarity."""

from src.normalizer import (
    calculate_artist_similarity,
    calculate_candidate_score,
    calculate_string_similarity,
    clean_artist,
    clean_title,
    extract_primary_artist,
    is_duration_matching,
    normalize_unicode,
    verify_track_match,
)


def test_normalize_unicode():
    assert normalize_unicode("Beyoncé") == "Beyoncé"
    assert normalize_unicode("Song\u00A0Title") == "Song Title"
    assert normalize_unicode("  Trim   Spaces  ") == "Trim Spaces"


def test_clean_title():
    # Remaster variants
    assert clean_title("Bohemian Rhapsody - 2011 Remaster") == "Bohemian Rhapsody"
    assert clean_title("Comfortably Numb (2011 Digital Remaster)") == "Comfortably Numb"
    assert clean_title("Starman (Remastered 2012)") == "Starman"
    assert clean_title("Song [Deluxe Edition]") == "Song"
    
    # Feat variants
    assert clean_title("Closer (feat. Halsey)") == "Closer"
    assert clean_title("Stan ft. Dido") == "Stan"
    assert clean_title("Track (Featuring Various Artists)") == "Track"

    # Quality & video annotations
    assert clean_title("Numb [Official Music Video]") == "Numb"
    assert clean_title("In The End (Official Audio)") == "In The End"
    assert clean_title("Song (Lyric Video)") == "Song"
    assert clean_title("Song (HD 4K)") == "Song"

    # Track number prefixes vs numerical song titles
    assert clean_title("01 - Stairway to Heaven") == "Stairway to Heaven"
    assert clean_title("05. Hotel California") == "Hotel California"
    assert clean_title("12 - Song Title") == "Song Title"
    assert clean_title("01 Song Title") == "Song Title"
    assert clean_title("30 Hours") == "30 Hours"
    assert clean_title("21 Guns") == "21 Guns"
    assert clean_title("99 Problems") == "99 Problems"


def test_clean_artist():
    assert clean_artist("The Chainsmokers feat. Halsey") == "The Chainsmokers"
    assert clean_artist("Eminem ft. Rihanna") == "Eminem"
    assert clean_artist("Artist A / Artist B") == "Artist A, Artist B"


def test_extract_primary_artist():
    assert extract_primary_artist("Queen & David Bowie") == "Queen"
    assert extract_primary_artist("Daft Punk feat. Pharrell Williams") == "Daft Punk"
    assert extract_primary_artist("Taylor Swift, Bon Iver") == "Taylor Swift"


def test_duration_matching():
    assert is_duration_matching(240.0, 241.5, tolerance_seconds=2.5) is True
    assert is_duration_matching(240.0, 243.0, tolerance_seconds=2.5) is False
    assert is_duration_matching(240.0, None) is True
    assert is_duration_matching(240.0, 0.0) is True


def test_string_similarity():
    assert calculate_string_similarity("Hello World", "Hello World") == 1.0
    assert calculate_string_similarity("Bohemian Rhapsody", "bohemian rhapsody") == 1.0
    assert calculate_string_similarity("Song (Remastered)", "Song") >= 0.75
    assert calculate_string_similarity("Totally Different", "Completely Unrelated") < 0.45


def test_verify_track_match():
    # Valid match
    is_match, score, reason = verify_track_match(
        expected_title="Bohemian Rhapsody (2011 Remaster)",
        expected_artist="Queen",
        found_title="Bohemian Rhapsody",
        found_artist="Queen",
        expected_duration=354.0,
        found_duration=355.0,
        tolerance_seconds=2.5,
    )
    assert is_match is True
    assert score >= 0.8

    # Duration mismatch
    is_match, score, reason = verify_track_match(
        expected_title="Bohemian Rhapsody",
        expected_artist="Queen",
        found_title="Bohemian Rhapsody",
        found_artist="Queen",
        expected_duration=354.0,
        found_duration=400.0,
        tolerance_seconds=2.5,
    )
    assert is_match is False
    assert "Duration mismatch" in reason

    # Completely different title
    is_match, score, reason = verify_track_match(
        expected_title="Yesterday",
        expected_artist="The Beatles",
        found_title="Hey Jude",
        found_artist="The Beatles",
        expected_duration=120.0,
        found_duration=120.0,
    )
    assert is_match is False


def test_calculate_artist_similarity():
    # Exact
    assert calculate_artist_similarity("Queen", "Queen") == 1.0
    # Alias / Transliteration
    assert calculate_artist_similarity("Kanye West", "Ye") == 1.0
    assert calculate_artist_similarity("Kanye West", "侃爷") == 1.0
    assert calculate_artist_similarity("Kanye West", "Ye (侃爷)、PARTYNEXTDOOR") == 1.0
    assert calculate_artist_similarity("Jay Chou", "周杰伦") == 1.0
    # Composite / featuring
    assert calculate_artist_similarity("Queen", "Queen & David Bowie") >= 0.85
    assert calculate_artist_similarity("David Bowie", "Queen & David Bowie") >= 0.85
    # Unrelated
    assert calculate_artist_similarity("Kanye West", "KOO's") < 0.35
    assert calculate_artist_similarity("Adele", "Lionel Richie") < 0.35


def test_calculate_candidate_score():
    # Exact match
    score = calculate_candidate_score("Ghost Town", "Kanye West", "Ghost Town", "Kanye West")
    assert score == 1.0

    # Cross-language / alias match
    score = calculate_candidate_score("Ghost Town", "Kanye West", "Ghost Town (Explicit)", "Ye (侃爷)、PARTYNEXTDOOR")
    assert score >= 0.85

    # Secondary artist match
    score = calculate_candidate_score("Under Pressure", "Queen", "Under Pressure", "Queen & David Bowie")
    assert score >= 0.85

    # Completely different artist when title matches -> must be rejected (0.0)
    score = calculate_candidate_score("Ghost Town", "Kanye West", "Ghost Town", "KOO's")
    assert score == 0.0

    score = calculate_candidate_score("Hello", "Adele", "Hello", "Lionel Richie")
    assert score == 0.0

    # Completely different song title when artist matches -> must be rejected (0.0)
    score = calculate_candidate_score("Ghost Town", "Kanye West", "Flashing Lights", "Ye (侃爷)")
    assert score == 0.0

    # Target artist empty -> accepts on title
    score = calculate_candidate_score("Ghost Town", "", "Ghost Town", "KOO's")
    assert score == 1.0


def test_safe_float():
    """Regression: safe_float must handle None, zero, valid floats, and string numbers."""
    from src.normalizer import safe_float
    # None input → None output
    assert safe_float(None) is None
    # Zero input → None output (0 means "unknown duration")
    assert safe_float(0) is None
    assert safe_float(0.0) is None
    # Valid positive float → returns float
    assert safe_float(120.5) == 120.5
    assert safe_float(1) == 1.0
    # String number → returns float
    assert safe_float("42.5") == 42.5
    # Invalid string → returns default
    assert safe_float("not_a_number") is None
    assert safe_float("not_a_number", 99.0) == 99.0
    # Custom default for zero
    assert safe_float(0, 0.0) is None
    assert safe_float(0, 99.0) == 99.0

