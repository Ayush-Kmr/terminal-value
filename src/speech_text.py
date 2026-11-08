"""Turn script narration into text a voice model reads correctly.

Scripts keep numbers as digits (the renderer puts them on screen). The cloned
voice model has no number reader of its own, so digits, currency and percent
signs are spelled out here, in the narration language, just before synthesis.
"""
from __future__ import annotations

import re

from num2words import num2words

HI_0_99 = (
    "शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह "
    "अठारह उन्नीस बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस "
    "इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस चालीस इकतालीस "
    "बयालीस तैंतालीस चौवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास पचास इक्यावन "
    "बावन तिरेपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरेसठ चौंसठ "
    "पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर "
    "छिहत्तर सतहत्तर अठहत्तर उन्यासी अस्सी इक्यासी बयासी तिरासी चौरासी पचासी छियासी "
    "सत्तासी अट्ठासी नवासी नब्बे इक्यानवे बानवे तिरानवे चौरानवे पंचानवे छियानवे "
    "सत्तानवे अट्ठानवे निन्यानवे"
).split()
HI_SCALES = ((10**9, "अरब"), (10**7, "करोड़"), (10**5, "लाख"), (10**3, "हज़ार"), (100, "सौ"))

CURRENCY = {
    "hi": {"$": "डॉलर", "₹": "रुपये", "€": "यूरो", "£": "पाउंड"},
    "en": {"$": "dollars", "₹": "rupees", "€": "euros", "£": "pounds"},
}
PERCENT = {"hi": "प्रतिशत", "en": "percent"}
POINT = {"hi": "दशमलव", "en": "point"}
SCALE_WORDS = (r"million|billion|trillion|thousand|lakh|crore|"
               r"मिलियन|बिलियन|ट्रिलियन|हज़ार|हजार|लाख|करोड़|अरब")

NUMBER = re.compile(
    rf"(?P<cur>[$₹€£])?\s?(?P<num>\d+(?:,\d+)*(?:\.\d+)?)(?P<pct>\s?%)?"
    rf"(?P<scale>\s?(?:{SCALE_WORDS})(?![\wऀ-ॿ]))?")


def hindi_int(n: int) -> str:
    if n < 100:
        return HI_0_99[n]
    parts = []
    for value, name in HI_SCALES:
        if n >= value:
            parts.append(f"{hindi_int(n // value)} {name}")
            n %= value
    if n:
        parts.append(HI_0_99[n])
    return " ".join(parts)


def _integer(n: int, language: str, is_year: bool) -> str:
    if language == "hi":
        # Years before 2000 are said in hundreds: 1991 is "उन्नीस सौ इक्यानवे".
        if is_year and 1100 <= n <= 1999:
            rest = f" {HI_0_99[n % 100]}" if n % 100 else ""
            return f"{HI_0_99[n // 100]} सौ{rest}"
        return hindi_int(n)
    words = num2words(n, lang="en", to="year") if is_year else num2words(n, lang="en")
    return words.replace(",", "")


def _number(raw: str, language: str, plain: bool) -> str:
    whole, _, frac = raw.replace(",", "").partition(".")
    is_year = plain and "," not in raw and not frac and len(whole) == 4 and 1100 <= int(whole) <= 2099
    words = _integer(int(whole), language, is_year)
    if frac:
        digit = (lambda d: HI_0_99[int(d)]) if language == "hi" else (
            lambda d: num2words(int(d), lang="en"))
        words += f" {POINT[language]} " + " ".join(digit(d) for d in frac)
    return words


DECADES = {"10": "tens", "20": "twenties", "30": "thirties", "40": "forties", "50": "fifties",
           "60": "sixties", "70": "seventies", "80": "eighties", "90": "nineties"}


def _english(text: str) -> str:
    """Finance shorthand a voice model reads wrongly if left as it is written."""
    text = re.sub(r"\b401\(k\)s?", lambda m: "four oh one K" + ("s" if m[0][-1] == "s" else ""),
                  text, flags=re.I)
    text = re.sub(r"\b403\(b\)", "four oh three B", text, flags=re.I)
    text = text.replace("AT&T", "A T and T").replace("S&P", "S and P")
    text = re.sub(r"\b([A-Z])&([A-Z])\b", r"\1 and \2", text)
    text = re.sub(r"\bQ([1-4])\b", lambda m: "Q " + num2words(int(m[1])), text)
    text = re.sub(r"\bFY\s?(\d{2})\b", lambda m: "fiscal " + num2words(int(m[1])), text)
    text = re.sub(r"\bFY\s?(\d{4})\b", r"fiscal \1", text)
    text = re.sub(r"\bU\.?S\.?(?=\s|$|[,;:)])(?!\s[a-z]+\s[A-Z]\.)", "U.S.", text)
    text = re.sub(r"\bP/E\b", "P E", text)
    text = text.replace("24/7", "twenty four seven")
    text = re.sub(r"\bvs\.?(?=\s)", "versus", text)
    text = re.sub(r"(?<![\w.])(\d{1,3}(?:,\d{3})*|\d+)(st|nd|rd|th)\b",
                  lambda m: num2words(int(m[1].replace(",", "")), to="ordinal"), text)
    text = re.sub(r"\b(19|20)([1-9]0)s\b", lambda m: num2words(int(m[1]), lang="en") + " "
                  + DECADES[m[2]], text)
    text = re.sub(r"\$(\d+)\.(\d{2})(?!\d)(?!\s?(?:thousand|million|billion|trillion))",
                  lambda m: f"{num2words(int(m[1]))} dollar{'' if m[1] == '1' else 's'} and "
                            f"{num2words(int(m[2]))} cent{'' if m[2] == '01' else 's'}", text)
    text = re.sub(r"(\d+(?:\.\d+)?)x\b", r"\1 times", text)
    text = text.replace("~", "about ").replace(" & ", " and ")
    return text


def spoken(text: str, language: str) -> str:
    """Narration with every figure written out in words."""
    text = re.sub(r"(\d)\.0+(?![\d.])", r"\1", str(text))   # "$3,958.00" is said "3,958"
    # Bhojpuri counts like Hindi, written in Devanagari.
    language = "hi" if language == "bho" else language if language in CURRENCY else "en"
    if language == "en":
        text = _english(text)

    def repl(m: re.Match) -> str:
        cur, pct, scale = m["cur"], m["pct"], (m["scale"] or "").strip()
        out = [_number(m["num"], language, plain=not (cur or pct or scale))]
        if scale:
            out.append(scale)
        if pct:
            out.append(PERCENT[language])
        if cur:
            out.append(CURRENCY[language][cur])
        return " " + " ".join(out) + " "

    text = NUMBER.sub(repl, re.sub(r"(?<=\w)-(?=\d)", " ", text))
    if language == "en":
        text = re.sub(r"\bone dollars\b", "one dollar", text)
    return re.sub(r"\s+([,.।?!;:])", r"\1", re.sub(r"\s{2,}", " ", text)).strip()
