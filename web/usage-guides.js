const GUIDE_START = "[Automatische Bedienhinweise]";
const GUIDE_END = "[/Automatische Bedienhinweise]";

const CATEGORY_GUIDES = [
  {
    keywords: ["scheinwerfer", "licht", "moving head", "beam", "wash", "spot", "blinder", "led"],
    title: "Lichttechnik",
    instructions: [
      "Vor dem Einsatz Gehäuse, Kabel und Befestigungen auf sichtbare Schäden prüfen; beschädigtes Gerät nicht verwenden.",
      "Nur die vom Hersteller vorgesehene Stromversorgung, Anschlüsse und Zubehör verwenden. Montage und Sicherung nur fachgerecht nach Herstellervorgaben durchführen.",
      "Lüftungsöffnungen freihalten und heiße Teile vor dem Berühren abkühlen lassen.",
      "Sicherheitsabstände einhalten und nicht direkt aus kurzer Entfernung in eine starke Lichtquelle blicken.",
    ],
  },
  {
    keywords: ["lautsprecher", "audio", "ton", "mischpult", "mikrofon", "microphone", "subwoofer", "endstufe", "verstärker", "dj"],
    title: "Tontechnik",
    instructions: [
      "Vor dem Anschließen die Lautstärke reduzieren beziehungsweise Ausgänge stummschalten; passende Ein- und Ausgänge sowie Kabel nach Herstellerangaben verwenden.",
      "Pegel vorsichtig erhöhen und Übersteuerung vermeiden. Bei Rückkopplungen die Lautstärke reduzieren.",
      "Gehör schützen und beschädigte Kabel oder Geräte nicht verwenden.",
    ],
  },
  {
    keywords: ["nebel", "fog", "hazer", "konfetti", "effektmaschine"],
    title: "Effekttechnik",
    instructions: [
      "Nur freigegebene Verbrauchsmaterialien verwenden und Sicherheits-, Lüftungs- sowie Abkühlhinweise des Herstellers beachten.",
      "Vor dem Einsatz die Veranstaltungsregeln und erforderliche Freigaben, etwa für Alarm- oder Brandschutzanlagen, klären.",
      "Gerät nicht unbeaufsichtigt betreiben und bei sichtbaren Schäden außer Betrieb nehmen.",
    ],
  },
  {
    keywords: ["video", "beamer", "projektor", "display", "kamera"],
    title: "Video- und Projektionstechnik",
    instructions: [
      "Gerät standsicher aufstellen oder fachgerecht befestigen und die Lüftungsöffnungen freihalten.",
      "Nur vorgesehene Strom- und Signalanschlüsse verwenden; vor dem Umstecken Herstellerhinweise beachten.",
      "Reinigung und Wartung nur wie vom Hersteller beschrieben durchführen.",
    ],
  },
];

const GENERAL_GUIDE = [
  "Vor dem Einsatz Gehäuse, Kabel und Anschlüsse auf sichtbare Schäden prüfen; beschädigtes Gerät nicht verwenden.",
  "Nur die vorgesehene Stromversorgung, Anschlüsse und Zubehör verwenden und die Hersteller-Sicherheitsvorgaben beachten.",
  "Gerät standsicher und bestimmungsgemäß verwenden. Elektrische Geräte nicht öffnen oder eigenmächtig verändern.",
  "Vor Reinigung oder Wartung ausschalten und vom Strom trennen; genaue Bedienung und Grenzwerte der Herstelleranleitung entnehmen.",
];

export function hasAutoUsageGuide(technicalData) {
  const text = technicalData || "";
  return text.includes(GUIDE_START) && text.includes(GUIDE_END);
}

export function manufacturerManualSearchUrl(name = "", manufacturer = "", model = "") {
  const product = [manufacturer, model, name].filter((part) => part?.trim()).map((part) => part.trim()).join(" ");
  const query = product ? `${product} Bedienungsanleitung` : "Bedienungsanleitung Veranstaltungstechnik";
  return `https://www.google.com/search?${new URLSearchParams({ q: query })}`;
}

export function updateAutoUsageGuide(technicalData, {
  enabled,
  name = "",
  manufacturer = "",
  model = "",
  category = "",
}) {
  const sectionPattern = new RegExp(
    `^\\s*${GUIDE_START.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*$[\\s\\S]*?^\\s*${GUIDE_END.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*$\\n?`,
    "gm",
  );
  const text = String(technicalData || "").replace(sectionPattern, "").trim();
  if (!enabled) return text;

  const productTerms = `${name} ${category}`.toLocaleLowerCase("de");
  const matchingGuide = CATEGORY_GUIDES.find((guide) =>
    guide.keywords.some((keyword) => productTerms.includes(keyword)),
  );
  const title = matchingGuide ? `Allgemeine Bedienhinweise – ${matchingGuide.title}` : "Allgemeine Bedienhinweise";
  const instructions = matchingGuide?.instructions || GENERAL_GUIDE;
  const searchUrl = manufacturerManualSearchUrl(name, manufacturer, model);
  const generated = [
    GUIDE_START,
    title,
    ...instructions.map((instruction) => `- ${instruction}`),
    "Die gerätespezifische Herstelleranleitung und deren Sicherheitsvorgaben sind maßgeblich.",
    "Herstelleranleitung suchen (Google; die Suchanfrage wird erst beim Öffnen übertragen):",
    searchUrl,
    GUIDE_END,
  ].join("\n");
  return text ? `${text}\n\n${generated}` : generated;
}
