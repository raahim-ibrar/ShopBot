import re
import unicodedata


INJECTION = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass|disable)\b.{0,45}"
    r"\b(?:instructions|system prompt|guardrails|safety rules|confirmation|budget limit)\b"
    r"|\b(?:show|reveal|print|repeat|export|return)\b.{0,45}"
    r"\b(?:system prompt|developer (?:message|instructions)|api key|database url|environment variables)\b"
    r"|(?:\[/?inst\]|<\|(?:system|developer)\|>|</?(?:system|developer)>)"
)
PRIVATE_DATA = re.compile(
    r"\b(?:another|other)\s+(?:user|customer|shopper|seller)(?:'s|s')?\b.{0,55}"
    r"\b(?:orders?|data|cart|preferences?|profile|history|threads?)\b"
)
OFF_TOPIC = re.compile(
    r"^(?:please\s+)?(?:write|compose|generate|solve|tell me)\s+"
    r"(?:(?:me|us)\s+)?(?:an?\s+|the\s+)?(?:poem|song|essay|math problem|joke|weather forecast|python (?:code|script))\b"
    r"|^(?:what is|what's)\s+(?:the weather|the capital of)\b"
)
CONFIRM_ORDER = re.compile(
    r"(?:yes(?:,? please)?|confirm|(?:i )?confirm (?:the |my )?order"
    r"|(?:yes[,.]? )?(?:please )?(?:place|submit) (?:the |my )?order)[.!]?"
)
QUOTE_ORDER = re.compile(
    r"(?:^|[.!?]\s+|\band\s+)(?:please\s+)?(?:prepare|quote|preview)\s+"
    r"(?:(?:a|the|my|this)\s+)?(?:demo\s+)?(?:order|checkout)\b"
)
SENSITIVE_CONTENT = re.compile(r"postgres(?:ql)?://|\bsk-ant-[a-z0-9_-]+|(?<!\d)(?:\d[ -]?){13,19}(?!\d)", re.I)
DEVICE_RECALL = re.compile(
    r"\bwhat (?:phone|device) (?:do i (?:use|have)|did i (?:ask|tell) (?:you|portpair) to remember|"
    r"did i (?:save|mention))\b"
    r"|\b(?:show|recall|tell me) (?:my|the) saved (?:phone|device)\b"
)


def _normalized(message: str) -> str:
    value = unicodedata.normalize("NFKC", message).casefold().replace("’", "'")
    visible = "".join(character for character in value if unicodedata.category(character) != "Cf")
    return " ".join(visible.split())


def refusal_for(message: str) -> str | None:
    value = _normalized(message)
    if SENSITIVE_CONTENT.search(value):
        return "Please remove credentials or payment details. PortPair demo orders don’t need them."
    if INJECTION.search(value):
        return "I can help with PortPair products and demo orders. I can’t reveal or change internal instructions or bypass safeguards."
    if PRIVATE_DATA.search(value):
        return "I can only help with your own PortPair cart and orders."
    if OFF_TOPIC.search(value):
        return "I can help with PortPair products, compatibility, stock, and demo orders."
    return None


def confirms_checkout(message: str) -> bool:
    return CONFIRM_ORDER.fullmatch(_normalized(message)) is not None


def asks_for_order_quote(message: str) -> bool:
    return QUOTE_ORDER.search(_normalized(message)) is not None


def asks_for_saved_device(message: str) -> bool:
    return DEVICE_RECALL.search(_normalized(message)) is not None
