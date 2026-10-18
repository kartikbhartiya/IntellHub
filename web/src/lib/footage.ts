/* Real recorded street footage that stands in for each camera's stream.
 *
 * Every clip is a 20-second window of a video of a Bengaluru street published on
 * Wikimedia Commons, re-encoded to 640x360 H.264 (public/cctv/<slug>.mp4). The boxes
 * drawn over it are real too: YOLO11s object detection with ByteTrack tracking, run on
 * the same encoded frames and stored per frame in public/cctv/tracks/<slug>.json.
 *
 * What it is not: the KSP Safe City feed. Nobody in these clips is a registry person,
 * so the matcher's face and plate alerts are never pinned on anyone in the picture.
 * The credits below are required by the CC BY / CC BY-SA licences and are shown in
 * the camera view.
 *
 * A camera that an administrator has attached a live stream to (lib/streams.ts) plays
 * that instead; its clip is then only the fallback shown, under a notice, while the
 * live link is down. Either way the tile is badged for what it is: RECORDED or LIVE. */

export type Light = "day" | "dusk" | "night";

export interface Clip {
  slug: string;
  place: string;
  light: Light;
  /** "sweep" clips were filmed panning, which is how a PTZ camera on patrol looks. */
  motion: "fixed" | "sweep";
  types: string[];
  /** Cameras whose name or area this footage was actually filmed at. */
  match?: RegExp;
  title: string;
  author: string;
  license: string;
  source: string;
}

const BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";
const commons = (file: string) => `https://commons.wikimedia.org/wiki/File:${file}`;

export const CLIPS: Clip[] = [
  { slug: "brigade-road-night", place: "Brigade Road", light: "night", motion: "fixed", types: ["PTZ", "FACE", "CCTV"], match: /Brigade Road/i,
    title: "Video of Brigade Road, Bangalore at night (2024)", author: "Gpkp", license: "CC BY-SA 4.0",
    source: commons("Video_of_Brigade_Road,_Bangalore_at_night_(2024).webm") },
  { slug: "mg-road-metro", place: "MG Road Metro, from Barton Centre", light: "day", motion: "fixed", types: ["FACE", "CCTV", "PTZ"], match: /\bMG Road\b/i,
    title: "Aerial view from Barton Centre, MG Road, Bangalore (2024) 01", author: "Gpkp", license: "CC BY-SA 4.0",
    source: commons("Aerial_view_from_Barton_Centre,_MG_Road,_Bangalore_(2024)_01.webm") },
  { slug: "banashankari-circle", place: "Banashankari traffic circle", light: "day", motion: "sweep", types: ["PTZ", "FACE"], match: /Banashankari/i,
    title: "Banashankari traffic circle, Banashankari, Bangalore (2024) 01", author: "Gpkp", license: "CC BY-SA 4.0",
    source: commons("Banashankari_traffic_circle,_Banashankari,_Bangalore_(2024)_01.ogg") },
  { slug: "banashankari-crossing", place: "Banashankari traffic circle", light: "day", motion: "sweep", types: ["FACE", "PTZ"],
    title: "Banashankari traffic circle, Banashankari, Bangalore (2024) 02", author: "Gpkp", license: "CC BY-SA 4.0",
    source: commons("Banashankari_traffic_circle,_Banashankari,_Bangalore_(2024)_02.ogg") },
  { slug: "shivajinagar-bus-station", place: "Shivajinagar Bus Station", light: "day", motion: "sweep", types: ["FACE", "PTZ"], match: /Shivajinagar Bus/i,
    title: "Shivaji Nagar Bus Station Bangalore - St. Marys Basilica - Cubbon Park", author: "Yathra Visheshangal", license: "CC BY 3.0",
    source: commons("Shivaji_Nagar_Bus_Station_Bangalore_-_St._Marys_Basilica_-_Cubbon_Park.webm") },
  { slug: "hebbal-road-1", place: "Hebbal Road", light: "day", motion: "fixed", types: ["ANPR", "CCTV"], match: /Hebbal/i,
    title: "Hebbal Road, Bangalore (2025) 01", author: "Gpkp", license: "CC BY-SA 4.0",
    source: commons("Hebbal_Road,_Bangalore_(2025)_01.webm") },
  { slug: "hebbal-road-2", place: "Hebbal Road", light: "day", motion: "fixed", types: ["ANPR"],
    title: "Hebbal Road, Bangalore (2025) 02", author: "Gpkp", license: "CC BY-SA 4.0",
    source: commons("Hebbal_Road,_Bangalore_(2025)_02.webm") },
  { slug: "hebbal-road-dusk", place: "Hebbal Road", light: "dusk", motion: "fixed", types: ["ANPR", "CCTV"],
    title: "Hebbal Road, Bangalore (2025) 03", author: "Gpkp", license: "CC BY-SA 4.0",
    source: commons("Hebbal_Road,_Bangalore_(2025)_03.webm") },
  { slug: "ramana-maharshi-road-night", place: "Ramana Maharshi Road, Sadashivanagar", light: "night", motion: "fixed", types: ["ANPR"],
    match: /Mekhri|Sadashivanagar/i,
    title: "Ramana Maharshi Road, Bangalore (2025) 01", author: "Gpkp", license: "CC BY-SA 4.0",
    source: commons("Ramana_Maharshi_Road,_Bangalore_(2025)_01.webm") },
  { slug: "ramana-maharshi-road-jam", place: "Ramana Maharshi Road, Sadashivanagar", light: "night", motion: "fixed", types: ["ANPR", "CCTV"],
    title: "Ramana Maharshi Road, Bangalore (2025) 02", author: "Gpkp", license: "CC BY-SA 4.0",
    source: commons("Ramana_Maharshi_Road,_Bangalore_(2025)_02.webm") },
  { slug: "dr-rajkumar-road", place: "Dr. Rajkumar Road, Rajajinagar", light: "day", motion: "fixed", types: ["ANPR", "CCTV"],
    title: "Dr. Rajkumar Road, Bangalore (2025)", author: "Gpkp", license: "CC BY-SA 4.0",
    source: commons("Dr._Rajkumar_Road,_Bangalore_(2025).webm") },
  { slug: "channasandra-night-market", place: "Channasandra", light: "night", motion: "sweep", types: ["PTZ", "CCTV", "FACE"],
    title: "Channasandra at night (2024)", author: "Gpkp", license: "CC BY-SA 4.0",
    source: commons("Channasandra_at_night_(2024).ogg") },
  { slug: "bsk-80ft-road-circle", place: "80 Feet Road circle, BSK 6th Stage", light: "day", motion: "fixed", types: ["CCTV", "ANPR"],
    title: "Aerial view near 80 feet road circle, BSK 6th stage, Bangalore (2025) 02", author: "Gpkp", license: "CC BY-SA 4.0",
    source: commons("Aerial_view_near_80_feet_road_circle,_BSK_6th_stage,_Bangalore_(2025)_02.webm") },
  { slug: "bsk-80ft-road", place: "80 Feet Road, BSK 6th Stage", light: "day", motion: "fixed", types: ["ANPR", "CCTV"],
    title: "Aerial view, 80 feet road, BSK 6th stage, Bangalore (2025) 01", author: "Gpkp", license: "CC BY-SA 4.0",
    source: commons("Aerial_view,_80_feet_road,_BSK_6th_stage,_Bangalore_(2025)_01.webm") },
  { slug: "bsk-6th-stage-road", place: "BSK 6th Stage", light: "day", motion: "fixed", types: ["ANPR", "CCTV"],
    title: "Aerial view, BSK 6th stage, Bangalore (2024) 03", author: "Gpkp", license: "CC BY-SA 4.0",
    source: commons("Aerial_view,_BSK_6th_stage,_Bangalore_(2024)_03.webm") },
  { slug: "bsk-6th-stage-dusk", place: "BSK 6th Stage", light: "dusk", motion: "fixed", types: ["CCTV", "ANPR"],
    title: "Aerial view, BSK 6th stage, Bangalore (2024) 12", author: "Gpkp", license: "CC BY-SA 4.0",
    source: commons("Aerial_view,_BSK_6th_stage,_Bangalore_(2024)_12.webm") },
];

export const footageUrl = (c: Clip) => `${BASE}/cctv/${c.slug}.mp4`;

export function hashUnit(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return (h >>> 0) / 4294967295;
}

type CamLike = { camera_id: string; name: string; area: string; type: string };

function locationClip(cam: CamLike) {
  const where = `${cam.name} ${cam.area}`;
  return CLIPS.find((c) => c.match?.test(where));
}

/** This camera's footage in its own preference order: the place it is named after, then
 *  the clips that suit its type, rotated by its id so neighbours start differently. */
function preferences(cam: CamLike): Clip[] {
  const pool = CLIPS.filter((c) => c.types.includes(cam.type));
  const list = pool.length ? pool : CLIPS;
  const k = Math.floor(hashUnit(cam.camera_id) * list.length);
  const rotated = [...list.slice(k), ...list.slice(0, k)];
  const here = locationClip(cam);
  return here ? [here, ...rotated.filter((c) => c !== here)] : rotated;
}

export function clipFor(cam: CamLike): Clip {
  return preferences(cam)[0];
}

/** Assign footage to a wall of cameras so that, as far as the catalogue allows, no two
 *  visible tiles show the same clip. Location matches are honoured first. */
export function assignClips(cams: CamLike[]): Record<string, Clip> {
  const out: Record<string, Clip> = {};
  const used = new Map<string, number>();
  const take = (cam: CamLike, clip: Clip) => {
    out[cam.camera_id] = clip;
    used.set(clip.slug, (used.get(clip.slug) || 0) + 1);
  };
  const rest: CamLike[] = [];
  for (const cam of cams) {
    const here = locationClip(cam);
    if (here && !used.has(here.slug)) take(cam, here);
    else rest.push(cam);
  }
  for (const cam of rest) {
    const prefs = preferences(cam);
    const fresh = prefs.find((c) => !used.has(c.slug));
    take(cam, fresh || prefs.reduce((a, b) => ((used.get(a.slug) || 0) <= (used.get(b.slug) || 0) ? a : b)));
  }
  return out;
}

// --------------------------------------------------------------------------- tracks
/** One detection: [track id, class index, x, y, w, h, confidence %], box in 0..1000 of the frame. */
export type Box = [number, number, number, number, number, number, number];

export interface Tracks {
  fps: number;
  classes: string[];
  model: string;
  frames: Box[][];
  tracks: Record<string, number>;
}

export const CLASS_STYLE: Record<string, { label: string; color: string }> = {
  person: { label: "person", color: "#30d158" },
  bicycle: { label: "bicycle", color: "#64d2ff" },
  car: { label: "car", color: "#0a84ff" },
  motorcycle: { label: "motorbike", color: "#ffd60a" },
  bus: { label: "bus", color: "#ff9f0a" },
  // COCO has no auto-rickshaw class; on Indian roads its "truck" is mostly autos and goods
  // carriers, so it is shown as what it certainly is.
  truck: { label: "vehicle", color: "#bf5af2" },
};

const cache = new Map<string, Promise<Tracks | null>>();

export function loadTracks(slug: string): Promise<Tracks | null> {
  let p = cache.get(slug);
  if (!p) {
    p = fetch(`${BASE}/cctv/tracks/${slug}.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    cache.set(slug, p);
  }
  return p;
}
