"""Line-oriented lexer for the an AWS-native Termin runtime DSL.

The an AWS-native Termin runtime DSL is line-structured. The lexer classifies each non-blank,
non-comment line by its leading keyword pattern, preserving the full text
and line number for the parser to do detailed extraction.
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Iterator
import re


class TokenType(Enum):
    # Header
    APPLICATION = auto()
    DESCRIPTION = auto()

    # Identity
    USERS_AUTHENTICATE = auto()
    SCOPES_ARE = auto()

    # Roles
    ROLE_DECL = auto()  # A "role" has ...

    # Content
    CONTENT_DECL = auto()  # Content called "name":
    FIELD_DECL = auto()    # Each X has a Y which is Z
    ACCESS_RULE = auto()   # Anyone with "scope" can ...

    # State
    STATE_DECL = auto()       # State for X called "Y":
    STATE_STARTS = auto()     # A X starts as "Y"
    STATE_ALSO = auto()       # A X can also be ...
    STATE_TRANSITION = auto() # A X Y can become Z if ...

    # Events
    EVENT_WHEN = auto()       # When a X is Y and Z:
    EVENT_ACTION = auto()     # Create a X with ...

    # User Stories
    STORY_HEADER = auto()     # As a X, I want to Y
    STORY_SO_THAT = auto()    # so that Z:
    SHOW_PAGE = auto()
    DISPLAY_TABLE = auto()
    SHOW_RELATED = auto()     # For each X, show Y grouped by Z
    HIGHLIGHT_ROWS = auto()
    ALLOW_FILTERING = auto()
    ALLOW_SEARCHING = auto()
    SUBSCRIBES_TO = auto()
    ACCEPT_INPUT = auto()
    VALIDATE_UNIQUE = auto()
    CREATE_AS = auto()
    AFTER_SAVING = auto()
    SHOW_CHART = auto()
    DISPLAY_AGGREGATION = auto()

    # Navigation
    NAV_BAR = auto()
    NAV_ITEM = auto()

    # API
    API_SECTION = auto()     # Expose a REST API at ...
    API_ENDPOINT = auto()    # GET /path description

    # Stream
    STREAM_DECL = auto()     # Stream X at Y

    # Fallback
    UNKNOWN = auto()


@dataclass
class Token:
    type: TokenType
    value: str  # full line text (stripped)
    line: int


# Patterns checked in order — first match wins.
# Each is (compiled regex on stripped line, token type).
_PATTERNS: list[tuple[re.Pattern, TokenType]] = [
    (re.compile(r'^Application:\s+'), TokenType.APPLICATION),
    (re.compile(r'^\s*Description:\s+'), TokenType.DESCRIPTION),
    (re.compile(r'^Users authenticate with\s+'), TokenType.USERS_AUTHENTICATE),
    (re.compile(r'^Scopes are\s+'), TokenType.SCOPES_ARE),
    (re.compile(r'^(?:A|An)\s+"[^"]+"\s+has\s+'), TokenType.ROLE_DECL),
    (re.compile(r'^Content called\s+"[^"]+"'), TokenType.CONTENT_DECL),
    (re.compile(r'^\s*Each\s+.+?\s+has\s+(?:a|an)\s+'), TokenType.FIELD_DECL),
    (re.compile(r'^\s*Anyone with\s+"[^"]+"\s+can\s+'), TokenType.ACCESS_RULE),
    (re.compile(r'^State for\s+\w+\s+called\s+"[^"]+"'), TokenType.STATE_DECL),
    (re.compile(r'^\s*(?:A|An)\s+\w+\s+starts\s+as\s+"[^"]+"'), TokenType.STATE_STARTS),
    (re.compile(r'^\s*(?:A|An)\s+\w+\s+can\s+also\s+be\s+'), TokenType.STATE_ALSO),
    (re.compile(r'^\s*(?:A|An)\s+.+?\s+can\s+become\s+'), TokenType.STATE_TRANSITION),
    (re.compile(r'^When\s+(?:a|an)\s+'), TokenType.EVENT_WHEN),
    (re.compile(r'^\s*Create\s+(?:a|an)\s+'), TokenType.EVENT_ACTION),
    (re.compile(r'^As\s+(?:a|an)\s+'), TokenType.STORY_HEADER),
    (re.compile(r'^\s*so\s+that\s+'), TokenType.STORY_SO_THAT),
    (re.compile(r'^\s*Show a page called\s+"[^"]+"'), TokenType.SHOW_PAGE),
    (re.compile(r'^\s*Display a table of\s+'), TokenType.DISPLAY_TABLE),
    (re.compile(r'^\s*For each\s+'), TokenType.SHOW_RELATED),
    (re.compile(r'^\s*Highlight rows where\s+'), TokenType.HIGHLIGHT_ROWS),
    (re.compile(r'^\s*Allow filtering by\s+'), TokenType.ALLOW_FILTERING),
    (re.compile(r'^\s*Allow searching by\s+'), TokenType.ALLOW_SEARCHING),
    (re.compile(r'^\s*This table subscribes to\s+'), TokenType.SUBSCRIBES_TO),
    (re.compile(r'^\s*Accept input for\s+'), TokenType.ACCEPT_INPUT),
    (re.compile(r'^\s*Validate that\s+'), TokenType.VALIDATE_UNIQUE),
    (re.compile(r'^\s*Create the\s+'), TokenType.CREATE_AS),
    (re.compile(r'^\s*After saving,\s+'), TokenType.AFTER_SAVING),
    (re.compile(r'^\s*Show a chart of\s+'), TokenType.SHOW_CHART),
    (re.compile(r'^\s*Display\s+'), TokenType.DISPLAY_AGGREGATION),
    (re.compile(r'^Navigation bar:'), TokenType.NAV_BAR),
    (re.compile(r'^\s*"[^"]+"\s+links to\s+'), TokenType.NAV_ITEM),
    (re.compile(r'^Expose a REST API at\s+'), TokenType.API_SECTION),
    (re.compile(r'^\s*(?:GET|POST|PUT|DELETE|PATCH)\s+/'), TokenType.API_ENDPOINT),
    (re.compile(r'^Stream\s+'), TokenType.STREAM_DECL),
]


def tokenize(source: str) -> list[Token]:
    """Tokenize a .termin source string into a list of Tokens."""
    tokens: list[Token] = []
    for line_num, raw_line in enumerate(source.splitlines(), start=1):
        stripped = raw_line.strip()

        # Skip blank lines and section comments (--- Section ---)
        if not stripped or stripped.startswith("---"):
            continue

        token_type = TokenType.UNKNOWN
        for pattern, ttype in _PATTERNS:
            if pattern.search(stripped):
                token_type = ttype
                break

        tokens.append(Token(type=token_type, value=stripped, line=line_num))

    return tokens
