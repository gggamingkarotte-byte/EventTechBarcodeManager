<<<<<<< HEAD
import re
from urllib.parse import urlencode


GUIDE_START = "[Automatische Bedienhinweise]"
GUIDE_END = "[/Automatische Bedienhinweise]"

_CATEGORY_GUIDES = (
    (
        ("scheinwerfer", "licht", "moving head", "beam", "wash", "spot", "blinder", "led"),
        "Lichttechnik",
        (
            "Vor dem Einsatz Gehäuse, Kabel und Befestigungen auf sichtbare Schäden prüfen; beschädigtes Gerät nicht verwenden.",
            "Nur die vom Hersteller vorgesehene Stromversorgung, Anschlüsse und Zubehör verwenden. Montage und Sicherung nur fachgerecht nach Herstellervorgaben durchführen.",
            "Lüftungsöffnungen freihalten und heiße Teile vor dem Berühren abkühlen lassen.",
            "Sicherheitsabstände einhalten und nicht direkt aus kurzer Entfernung in eine starke Lichtquelle blicken.",
        ),
    ),
    (
        ("lautsprecher", "audio", "ton", "mischpult", "mikrofon", "microphone", "subwoofer", "endstufe", "verstärker", "dj"),
        "Tontechnik",
        (
            "Vor dem Anschließen die Lautstärke reduzieren beziehungsweise Ausgänge stummschalten; passende Ein- und Ausgänge sowie Kabel nach Herstellerangaben verwenden.",
            "Pegel vorsichtig erhöhen und Übersteuerung vermeiden. Bei Rückkopplungen die Lautstärke reduzieren.",
            "Gehör schützen und beschädigte Kabel oder Geräte nicht verwenden.",
        ),
    ),
    (
        ("nebel", "fog", "hazer", "konfetti", "effektmaschine"),
        "Effekttechnik",
        (
            "Nur freigegebene Verbrauchsmaterialien verwenden und Sicherheits-, Lüftungs- sowie Abkühlhinweise des Herstellers beachten.",
            "Vor dem Einsatz die Veranstaltungsregeln und erforderliche Freigaben, etwa für Alarm- oder Brandschutzanlagen, klären.",
            "Gerät nicht unbeaufsichtigt betreiben und bei sichtbaren Schäden außer Betrieb nehmen.",
        ),
    ),
    (
        ("video", "beamer", "projektor", "display", "kamera"),
        "Video- und Projektionstechnik",
        (
            "Gerät standsicher aufstellen oder fachgerecht befestigen und die Lüftungsöffnungen freihalten.",
            "Nur vorgesehene Strom- und Signalanschlüsse verwenden; vor dem Umstecken Herstellerhinweise beachten.",
            "Reinigung und Wartung nur wie vom Hersteller beschrieben durchführen.",
        ),
    ),
)

_GENERAL_GUIDE = (
    "Vor dem Einsatz Gehäuse, Kabel und Anschlüsse auf sichtbare Schäden prüfen; beschädigtes Gerät nicht verwenden.",
    "Nur die vorgesehene Stromversorgung, Anschlüsse und Zubehör verwenden und die Hersteller-Sicherheitsvorgaben beachten.",
    "Gerät standsicher und bestimmungsgemäß verwenden. Elektrische Geräte nicht öffnen oder eigenmächtig verändern.",
    "Vor Reinigung oder Wartung ausschalten und vom Strom trennen; genaue Bedienung und Grenzwerte der Herstelleranleitung entnehmen.",
)


def has_auto_usage_guide(technical_data: str) -> bool:
    return GUIDE_START in (technical_data or "") and GUIDE_END in (technical_data or "")


def manufacturer_manual_search_url(name: str = "", manufacturer: str = "", model: str = "") -> str:
    product = " ".join(part.strip() for part in (manufacturer, model, name) if part and part.strip())
    query = f"{product} Bedienungsanleitung" if product else "Bedienungsanleitung Veranstaltungstechnik"
    return "https://www.google.com/search?" + urlencode({"q": query})


def update_auto_usage_guide(
    technical_data: str,
    *,
    enabled: bool,
    name: str = "",
    manufacturer: str = "",
    model: str = "",
    category: str = "",
) -> str:
    text = technical_data or ""
    section_pattern = re.compile(
        rf"(?ms)^\s*{re.escape(GUIDE_START)}\s*$.*?^\s*{re.escape(GUIDE_END)}\s*$\n?"
    )
    text = section_pattern.sub("", text).strip()
    if not enabled:
        return text

    product_terms = " ".join((name, category)).casefold()
    guide_title = "Allgemeine Bedienhinweise"
    instructions = _GENERAL_GUIDE
    for keywords, title, category_instructions in _CATEGORY_GUIDES:
        if any(keyword in product_terms for keyword in keywords):
            guide_title = f"Allgemeine Bedienhinweise – {title}"
            instructions = category_instructions
            break

    search_url = manufacturer_manual_search_url(name, manufacturer, model)
    generated = "\n".join((
        GUIDE_START,
        guide_title,
        *(f"- {instruction}" for instruction in instructions),
        "Die gerätespezifische Herstelleranleitung und deren Sicherheitsvorgaben sind maßgeblich.",
        "Herstelleranleitung suchen (Google; die Suchanfrage wird erst beim Öffnen übertragen):",
        search_url,
        GUIDE_END,
    ))
    return f"{text}\n\n{generated}" if text else generated
=======
import re
from urllib.parse import urlencode


GUIDE_START = "[Automatische Bedienhinweise]"
GUIDE_END = "[/Automatische Bedienhinweise]"

_CATEGORY_GUIDES = (
    (
        ("scheinwerfer", "licht", "moving head", "beam", "wash", "spot", "blinder", "led"),
        "Lichttechnik",
        (
            "Vor dem Einsatz Gehäuse, Kabel und Befestigungen auf sichtbare Schäden prüfen; beschädigtes Gerät nicht verwenden.",
            "Nur die vom Hersteller vorgesehene Stromversorgung, Anschlüsse und Zubehör verwenden. Montage und Sicherung nur fachgerecht nach Herstellervorgaben durchführen.",
            "Lüftungsöffnungen freihalten und heiße Teile vor dem Berühren abkühlen lassen.",
            "Sicherheitsabstände einhalten und nicht direkt aus kurzer Entfernung in eine starke Lichtquelle blicken.",
        ),
    ),
    (
        ("lautsprecher", "audio", "ton", "mischpult", "mikrofon", "microphone", "subwoofer", "endstufe", "verstärker", "dj"),
        "Tontechnik",
        (
            "Vor dem Anschließen die Lautstärke reduzieren beziehungsweise Ausgänge stummschalten; passende Ein- und Ausgänge sowie Kabel nach Herstellerangaben verwenden.",
            "Pegel vorsichtig erhöhen und Übersteuerung vermeiden. Bei Rückkopplungen die Lautstärke reduzieren.",
            "Gehör schützen und beschädigte Kabel oder Geräte nicht verwenden.",
        ),
    ),
    (
        ("nebel", "fog", "hazer", "konfetti", "effektmaschine"),
        "Effekttechnik",
        (
            "Nur freigegebene Verbrauchsmaterialien verwenden und Sicherheits-, Lüftungs- sowie Abkühlhinweise des Herstellers beachten.",
            "Vor dem Einsatz die Veranstaltungsregeln und erforderliche Freigaben, etwa für Alarm- oder Brandschutzanlagen, klären.",
            "Gerät nicht unbeaufsichtigt betreiben und bei sichtbaren Schäden außer Betrieb nehmen.",
        ),
    ),
    (
        ("video", "beamer", "projektor", "display", "kamera"),
        "Video- und Projektionstechnik",
        (
            "Gerät standsicher aufstellen oder fachgerecht befestigen und die Lüftungsöffnungen freihalten.",
            "Nur vorgesehene Strom- und Signalanschlüsse verwenden; vor dem Umstecken Herstellerhinweise beachten.",
            "Reinigung und Wartung nur wie vom Hersteller beschrieben durchführen.",
        ),
    ),
)

_GENERAL_GUIDE = (
    "Vor dem Einsatz Gehäuse, Kabel und Anschlüsse auf sichtbare Schäden prüfen; beschädigtes Gerät nicht verwenden.",
    "Nur die vorgesehene Stromversorgung, Anschlüsse und Zubehör verwenden und die Hersteller-Sicherheitsvorgaben beachten.",
    "Gerät standsicher und bestimmungsgemäß verwenden. Elektrische Geräte nicht öffnen oder eigenmächtig verändern.",
    "Vor Reinigung oder Wartung ausschalten und vom Strom trennen; genaue Bedienung und Grenzwerte der Herstelleranleitung entnehmen.",
)


def has_auto_usage_guide(technical_data: str) -> bool:
    return GUIDE_START in (technical_data or "") and GUIDE_END in (technical_data or "")


def manufacturer_manual_search_url(name: str = "", manufacturer: str = "", model: str = "") -> str:
    product = " ".join(part.strip() for part in (manufacturer, model, name) if part and part.strip())
    query = f"{product} Bedienungsanleitung" if product else "Bedienungsanleitung Veranstaltungstechnik"
    return "https://www.google.com/search?" + urlencode({"q": query})


def update_auto_usage_guide(
    technical_data: str,
    *,
    enabled: bool,
    name: str = "",
    manufacturer: str = "",
    model: str = "",
    category: str = "",
) -> str:
    text = technical_data or ""
    section_pattern = re.compile(
        rf"(?ms)^\s*{re.escape(GUIDE_START)}\s*$.*?^\s*{re.escape(GUIDE_END)}\s*$\n?"
    )
    text = section_pattern.sub("", text).strip()
    if not enabled:
        return text

    product_terms = " ".join((name, category)).casefold()
    guide_title = "Allgemeine Bedienhinweise"
    instructions = _GENERAL_GUIDE
    for keywords, title, category_instructions in _CATEGORY_GUIDES:
        if any(keyword in product_terms for keyword in keywords):
            guide_title = f"Allgemeine Bedienhinweise – {title}"
            instructions = category_instructions
            break

    search_url = manufacturer_manual_search_url(name, manufacturer, model)
    generated = "\n".join((
        GUIDE_START,
        guide_title,
        *(f"- {instruction}" for instruction in instructions),
        "Die gerätespezifische Herstelleranleitung und deren Sicherheitsvorgaben sind maßgeblich.",
        "Herstelleranleitung suchen (Google; die Suchanfrage wird erst beim Öffnen übertragen):",
        search_url,
        GUIDE_END,
    ))
    return f"{text}\n\n{generated}" if text else generated
>>>>>>> 6d3d503baf270319461af8f5d74d630d9001f251
