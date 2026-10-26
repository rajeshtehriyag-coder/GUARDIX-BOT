"""GUARDIX built-in anti-abuse list + matcher (Hindi / Hinglish / English).

Pure python (no telegram imports) so it can be unit-tested on its own.
Admins can add more words with /addbadword and exclude built-in ones with /delbadword.
"""
import re

# ── built-in words (lowercase, romanised + a few Devanagari) ──
WORDS = """
madarchod maderchod madarchoot behenchod bhenchod benchod behnchod bhosdike bhosdi bhosda bhosadi bhosdika
bhosdiwala bhosad bhosdo chutiya chutiye chutiyapa chut chutad chutmarika gandu gaandu gand gaand gandmara
gaandmara gandfat gaandfat lund lauda laude lode loda lodu lawda lavda randi randwa randikhana
haramkhor haramzada haramzade harami kamina kamine kamini kuttiya bhadwa bhadwe bhadve bhadwi hijda
kanjar kanjri gashti veshya chinal chinaal rakhail jhaatu jhatu jhant chudai mc bc bkl bsdk mkc bkc
fuck fucker fuckers fucking fuk fck fuckoff motherfucker motherfucking bitch bitches bastard asshole
dick dickhead cock pussy slut whore cunt shit bullshit shithead dumbass jackass nigger nigga faggot
retard wanker twat prick douchebag sonofabitch
मादरचोद बहनचोद भोसड़ीके भोसड़ी चूतिया चूत गांडू गांड लंड रंडी हरामी कमीना भड़वा कुतिया
chutiyon chutiyo chutan chutmarani chotiya chotiye chotia chutiyagiri madarjat maadarjaat baapchod bhosdiwale
bhosdiwalon bhosadike bhosri bhosriwala bhosre bhosdeke bhenkelode bhenkelaude bhenlund makelode makichut makichoot
machudi machudai machuda kutiya kaminey haramzadi haramjada haramjade haramii gandupan gandwa gandwe
gandiya lavde lawde lodey lundkhor lundchus lundfakeer chudasi chudwana randee randibaaz randibaz
jhantu jhantoo tharki bhadwagiri bhadwiya gandmasti gaandmasti lauday lodoo bkchod bkchodi mchod
mkl bhosdika bhsdk bsdke bsdwale fuckin fuckface fuckwit fuckup motherfucka motherfuckers mofo
bastards asshat arsehole dumbfuck dipshit shitface shitbag cocksucker cocksuckers blowjob handjob jerkoff
wankers dickheads pricks twats cunts sluts whores pussies niggers niggas faggots retards
retarded kys लौड़ा लौड़े हरामज़ादा हरामखोर कमीने चूतिये भोसड़ीवाले रंडीखाना बहनचोदों मादरचोदों
""".split()

# ── multi-word phrases ──
PHRASES = [
    "maa ki chut", "ma ki chut", "maa ka bhosda", "ma ka bhosda",
    "behen ke lode", "bhen ke lode", "maa chod", "ma chod", "behen chod",
    "gaand mara", "gand mara", "bhosdi ke", "madar chod", "bhen chod", "behen chod", "chut ke",
    'kutte ki aulad', 'harami ki aulad', 'kutte ke pille', 'bahen ke lode', 'maa ke lode', 'bhosdi wale', 'chut ke dhakkan', 'teri maa chod', 'kill yourself', 'go kill yourself', 'behen ki chut', 'bhen ki chut', 'bahan ki chut', 'behen ka bhosda', 'maa ka lauda', 'behen ka lauda', 'bhen ke takke', 'behen ke takke', 'madar chod', 'gaand marao', 'gand marao', 'chus le', 'lund le', 'lund choos',
]

_LEET = str.maketrans({"@": "a", "4": "a", "0": "o", "1": "i", "!": "i", "3": "e", "$": "s", "5": "s", "7": "t", "8": "b"})
_ZW = re.compile("[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_SPLIT = re.compile(r"[\s.,;:?()\[\]{}\"'`~^&*+=|\\/<>_\-]+")
_REPEAT = re.compile(r"(.)\1+")


def canon(word):
    """lowercase, leetspeak -> letters, repeated letters collapsed (maaadarchoood -> madarchod)."""
    w = _ZW.sub("", (word or "").lower()).translate(_LEET)
    return _REPEAT.sub(r"\1", w)


CANON = {canon(w) for w in WORDS}
LONG = sorted({c for c in CANON if len(c) >= 6 and c.isascii()}, key=len, reverse=True)
_PHR = [canon(p) for p in PHRASES]


def builtin_has(word):
    return canon(word) in CANON


def find_bad(text, exclude=()):
    """Return the matched built-in word / phrase, or None. `exclude` = set of canon() words an admin allowed."""
    if not text:
        return None
    ex = set(exclude or ())
    low = _ZW.sub("", text.lower())
    toks = [canon(t) for t in _SPLIT.split(low) if t]
    for t in toks:
        if t in CANON and t not in ex:
            return t
        if len(t) >= 6 and t.isascii():
            for w in LONG:
                if w in t and w not in ex:
                    return w
    spaced = " ".join(toks)
    for p in _PHR:
        if p not in ex and re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", spaced):
            return p
    # evasion like "m a d a r c h o d" : look at the squeezed text for long words only
    squeezed = re.sub(r"[^a-z0-9]", "", canon(low))
    if len(squeezed) >= 8:
        for w in LONG:
            if len(w) >= 8 and w in squeezed and w not in ex:
                return w
    return None


if __name__ == "__main__":  # quick self-test:  python words.py
    bad = ["madarchod", "m@darch0d", "maaadarchoood", "tu chutiya hai", "BHOSDIKE", "m a d a r c h o d", "fuuuck you",
            "बहनचोद", "oye bc", "motherfucker", "sala bkl hai"]
    good = ["bhai kya haal hai", "chutney khayenge", "assistant manager", "cocktail party", "class lagi hai", "dickens novel",
            "chod do yaar", "mcdonalds chalo", "rand currency rate", "good morning everyone", "kitna time lagega", "shitake mushroom"]
    fails = [t for t in bad if not find_bad(t)] + [t for t in good if find_bad(t)]
    print("words:", len(WORDS), "phrases:", len(PHRASES), "| FAIL:", fails or "none")
