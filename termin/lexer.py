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
    ROLE_ALIAS = auto()  # "clerk" is alias for "warehouse clerk"
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
    EVENT_WHEN = auto()       # When a X is Y and Z: / When [jexl]:
    EVENT_ACTION = auto()     # Create a X with ...

    # JEXL
    JEXL_BLOCK = auto()       # [expression] standalone line

    # User Stories
    STORY_HEADER = auto()     # As a X, I want to Y / As anonymous, I want to Y
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
    DISPLAY_TEXT = auto()        # Display text "..."
    DISPLAY_AGGREGATION = auto()

    # Navigation
    NAV_BAR = auto()
    NAV_ITEM = auto()

    # API
    API_SECTION = auto()     # Expose a REST API at ...
    API_ENDPOINT = auto()    # GET /path description

    # Stream
    STREAM_DECL = auto()     # Stream X at Y

    # Compute
    COMPUTE_DECL = auto()    # Compute called "name":
    COMPUTE_SHAPE = auto()   # Transform: / Reduce: / etc.

    # Channel
    CHANNEL_DECL = auto()    # Channel called "name":
    CHANNEL_CARRIES = auto() # Carries <content>
    CHANNEL_DIRECTION = auto() # Direction: inbound | outbound | bidirectional | internal
    CHANNEL_DELIVERY = auto()  # v2: Delivery: realtime | reliable | batch | auto
    CHANNEL_REQUIRES = auto()  # Requires "scope" to send/receive
    CHANNEL_ENDPOINT = auto()  # Endpoint: /path

    # Boundary
    BOUNDARY_DECL = auto()     # Boundary called "name":
    BOUNDARY_CONTAINS = auto() # Contains X, Y, and Z
    BOUNDARY_IDENTITY = auto() # Identity inherits/restricts
    BOUNDARY_EXPOSES = auto()  # Exposes property "name" : type = [jexl]

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
    (re.compile(r'^"[^"]+"\s+is\s+alias\s+for\s+"[^"]+"'), TokenType.ROLE_ALIAS),
    (re.compile(r'^(?:A|An)\s+"[^"]+"\s+has\s+'), TokenType.ROLE_DECL),
    (re.compile(r'^\w+\s+has\s+"[^"]+"'), TokenType.ROLE_DECL),  # Bare role: Anonymous has "scope"
    (re.compile(r'^Content called\s+"[^"]+"'), TokenType.CONTENT_DECL),
    (re.compile(r'^\s*Each\s+.+?\s+has\s+(?:a|an)\s+'), TokenType.FIELD_DECL),
    (re.compile(r'^\s*Anyone with\s+"[^"]+"\s+can\s+'), TokenType.ACCESS_RULE),
    (re.compile(r'^State for\s+.+?\s+called\s+"[^"]+"'), TokenType.STATE_DECL),
    (re.compile(r'^\s*(?:A|An)\s+\w+\s+starts\s+as\s+"[^"]+"'), TokenType.STATE_STARTS),
    (re.compile(r'^\s*(?:A|An)\s+\w+\s+can\s+also\s+be\s+'), TokenType.STATE_ALSO),
    (re.compile(r'^\s*(?:A|An)\s+.+?\s+can\s+become\s+'), TokenType.STATE_TRANSITION),
    (re.compile(r'^When\s+\['), TokenType.EVENT_WHEN),  # v2: When [jexl]:
    (re.compile(r'^When\s+(?:a|an)\s+'), TokenType.EVENT_WHEN),  # v1: When a X is Y
    (re.compile(r'^\s*Create\s+(?:a|an)\s+'), TokenType.EVENT_ACTION),
    (re.compile(r'^As\s+(?:(?:a|an)\s+)?\w'), TokenType.STORY_HEADER),
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
    (re.compile(r'^\s*Display\s+text\s+'), TokenType.DISPLAY_TEXT),
    (re.compile(r'^\s*Display\s+'), TokenType.DISPLAY_AGGREGATION),
    (re.compile(r'^Navigation bar:'), TokenType.NAV_BAR),
    (re.compile(r'^\s*"[^"]+"\s+links to\s+'), TokenType.NAV_ITEM),
    (re.compile(r'^Expose a REST API at\s+'), TokenType.API_SECTION),
    (re.compile(r'^\s*(?:GET|POST|PUT|DELETE|PATCH)\s+/'), TokenType.API_ENDPOINT),
    (re.compile(r'^Stream\s+'), TokenType.STREAM_DECL),
    # Compute
    (re.compile(r'^Compute called\s+"[^"]+"'), TokenType.COMPUTE_DECL),
    (re.compile(r'^\s*(?:Transform|Reduce|Expand|Correlate|Route):\s+'), TokenType.COMPUTE_SHAPE),
    # Channel
    (re.compile(r'^Channel called\s+"[^"]+"'), TokenType.CHANNEL_DECL),
    (re.compile(r'^\s*Carries\s+'), TokenType.CHANNEL_CARRIES),
    (re.compile(r'^\s*Direction:\s+'), TokenType.CHANNEL_DIRECTION),
    (re.compile(r'^\s*Delivery:\s+'), TokenType.CHANNEL_DELIVERY),
    (re.compile(r'^\s*Requires\s+"[^"]+"\s+to\s+'), TokenType.CHANNEL_REQUIRES),
    (re.compile(r'^\s*Endpoint:\s+'), TokenType.CHANNEL_ENDPOINT),
    # Boundary
    (re.compile(r'^Boundary called\s+"[^"]+"'), TokenType.BOUNDARY_DECL),
    (re.compile(r'^\s*Contains\s+'), TokenType.BOUNDARY_CONTAINS),
    (re.compile(r'^\s*Identity\s+(?:inherits|restricts)'), TokenType.BOUNDARY_IDENTITY),
    (re.compile(r'^\s*Exposes\s+property\s+'), TokenType.BOUNDARY_EXPOSES),
    # JEXL blocks — standalone [expression] lines (in Compute bodies etc.)
    (re.compile(r'^\s*\[.+\]\s*$'), TokenType.JEXL_BLOCK),
]


def tokenize(source: str) -> list[Token]:
    """Tokenize a .termin source string into a list of Tokens."""
    tokens: list[Token] = []
    for line_num, raw_line in enumerate(source.splitlines(), start=1):
        stripped = raw_line.strip()

        # Skip blank lines, section comments (--- Section ---), and parenthesis comments
        if not stripped or stripped.startswith("---"):
            continue
        if stripped.startswith("(") and stripped.endswith(")"):
            continue

        # Strip inline parenthesis comments: "text (comment)" -> "text"
        # Only strip if preceded by whitespace (not function calls like SayHello(...))
        value = re.sub(r'\s+\([^)]*\)\s*$', '', stripped).strip()

        token_type = TokenType.UNKNOWN
        for pattern, ttype in _PATTERNS:
            if pattern.search(value):
                token_type = ttype
                break

        tokens.append(Token(type=token_type, value=value, line=line_num))

    return tokens
