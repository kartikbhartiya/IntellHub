"use client";

/* Photographs of people on an FIR.
 *
 * PhotoField takes a picture from a file or the station's camera, re-encodes it in the
 * browser (at most 900 px on the long side, JPEG) and uploads it to POST /fir/photo.
 * Re-encoding through a canvas drops the camera's EXIF block — GPS fix, device serial —
 * before anything leaves the machine; the server strips metadata again regardless.
 *
 * FirPhoto shows a stored photograph. An <img src> cannot carry the bearer token, so the
 * image is fetched through apiFetch and shown from a blob URL released on unmount.
 */

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Camera, ImagePlus, Loader2, RefreshCw, UserRound, X } from "lucide-react";
import { apiFetch, apiFetchRaw } from "@/lib/api";

export type PhotoValue = { photo_id: string; preview: string };

const MAX_SIDE = 900;
const QUALITY = 0.85;

/** Draw any decodable image to a canvas no larger than MAX_SIDE and return JPEG bytes + a preview. */
async function normalise(source: CanvasImageSource, w: number, h: number) {
  const scale = Math.min(1, MAX_SIDE / Math.max(w, h));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(w * scale));
  canvas.height = Math.max(1, Math.round(h * scale));
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("This browser cannot process images.");
  ctx.fillStyle = "#fff"; // transparent PNGs would otherwise turn black in JPEG
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise<Blob | null>((res) => canvas.toBlob(res, "image/jpeg", QUALITY));
  if (!blob) throw new Error("The image could not be encoded.");
  return { blob, preview: canvas.toDataURL("image/jpeg", 0.7) };
}

async function fromFile(file: File) {
  if (!file.type.startsWith("image/")) throw new Error("Choose an image file.");
  if (file.size > 25_000_000) throw new Error("That file is too large (over 25 MB).");
  let bmp: ImageBitmap;
  try {
    // Honours the EXIF orientation, so phone photos are not stored sideways.
    bmp = await createImageBitmap(file, { imageOrientation: "from-image" } as ImageBitmapOptions);
  } catch {
    throw new Error("This image format cannot be read here — use a JPEG or PNG.");
  }
  try {
    return await normalise(bmp, bmp.width, bmp.height);
  } finally {
    bmp.close();
  }
}

async function upload(blob: Blob): Promise<string> {
  const fd = new FormData();
  fd.append("photo", blob, "photo.jpg");
  const res = await apiFetchRaw("/fir/photo", { method: "POST", body: fd });
  const data = await res.json().catch(() => ({}));
  if (res.ok && data.photo_id) return data.photo_id;
  if (res.status === 403) throw new Error("Your role cannot attach photographs.");
  throw new Error(data.message || `Upload failed (${res.status}).`);
}

// ---------------------------------------------------------------------------- camera
function CameraDialog({ onShot, onClose }: { onShot: (v: { blob: Blob; preview: string }) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const [facing, setFacing] = useState<"user" | "environment">("user");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let live = true;
    setReady(false);
    setErr(null);
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: facing, width: { ideal: 1280 }, height: { ideal: 960 } }, audio: false })
      .then((s) => {
        if (!live) return s.getTracks().forEach((t) => t.stop());
        stream = s;
        const v = video.current;
        if (v) {
          v.srcObject = s;
          v.play().catch(() => {});
        }
      })
      .catch(() => setErr("The camera could not be opened. Check the browser's camera permission, or upload a file instead."));
    return () => {
      live = false;
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [facing]);

  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  const shoot = async () => {
    const v = video.current;
    if (!v || !v.videoWidth) {
      setErr("The camera has not sent a picture yet — wait a moment and try again.");
      return;
    }
    try {
      onShot(await normalise(v, v.videoWidth, v.videoHeight));
    } catch (e: any) {
      setErr(e?.message || "Could not capture the photo.");
    }
  };

  // Portalled to <body>: inside <main> the fixed nav and Kavya bar would sit on top of it.
  return createPortal(
    <div className="fixed inset-0 z-[90] grid place-items-center bg-[#0b0e14]/60 p-4 backdrop-blur-sm" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="w-full max-w-md overflow-hidden rounded-2xl bg-white shadow-2xl">
        <div className="relative aspect-[4/3] bg-black">
          <video ref={video} autoPlay playsInline muted onLoadedData={() => setReady(true)} className="h-full w-full object-cover" style={{ transform: facing === "user" ? "scaleX(-1)" : undefined }} />
          {!ready && !err && (
            <p className="absolute inset-0 grid place-items-center text-[12.5px] text-white/70">
              <span className="inline-flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" /> Starting camera…</span>
            </p>
          )}
          {err && <p className="absolute inset-x-4 top-1/2 -translate-y-1/2 rounded-xl bg-black/70 p-3 text-center text-[12.5px] text-white">{err}</p>}
        </div>
        <div className="flex items-center gap-2 p-3">
          <button onClick={() => setFacing((f) => (f === "user" ? "environment" : "user"))} className="grid h-10 w-10 place-items-center rounded-xl border border-black/10 text-muted hover:text-ink" title="Switch camera" aria-label="Switch camera">
            <RefreshCw className="h-4 w-4" />
          </button>
          <button onClick={onClose} className="rounded-xl px-3 py-2 text-[13px] text-muted hover:text-ink">Cancel</button>
          <button onClick={shoot} disabled={!ready} className="ml-auto inline-flex items-center gap-1.5 rounded-xl bg-ink px-4 py-2.5 text-[13px] font-semibold text-white disabled:opacity-40">
            <Camera className="h-4 w-4" /> Capture
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------------------- field
export function PhotoField({ value, onChange, error, who }: {
  value?: PhotoValue | null;
  onChange: (v: PhotoValue | null) => void;
  error?: string;
  who: string;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [camOpen, setCamOpen] = useState(false);
  const [canCam, setCanCam] = useState(false);
  useEffect(() => setCanCam(typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia), []);

  const take = async (make: () => Promise<{ blob: Blob; preview: string }>) => {
    setBusy(true);
    setMsg(null);
    try {
      const { blob, preview } = await make();
      onChange({ photo_id: await upload(blob), preview });
    } catch (e: any) {
      setMsg(e?.message || "The photo could not be added.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="flex items-center gap-3">
        <div className="relative h-16 w-16 shrink-0 overflow-hidden rounded-xl border border-black/10 bg-black/[0.03]">
          {value?.preview ? (
            // eslint-disable-next-line @next/next/no-img-element -- local data URL preview
            <img src={value.preview} alt={`Photograph of the ${who}`} className="h-full w-full object-cover" />
          ) : (
            <span className="grid h-full w-full place-items-center text-faint">
              {busy ? <Loader2 className="h-5 w-5 animate-spin" /> : <UserRound className="h-6 w-6" />}
            </span>
          )}
          {value && !busy && (
            <button type="button" onClick={() => onChange(null)} aria-label={`Remove the ${who}'s photograph`}
                    className="absolute right-0.5 top-0.5 grid h-5 w-5 place-items-center rounded-full bg-black/60 text-white hover:bg-crimson">
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5">
          <button type="button" disabled={busy} onClick={() => fileRef.current?.click()}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-black/10 bg-white px-2.5 py-1.5 text-[12px] font-medium text-ink hover:border-brand/40 disabled:opacity-50">
            <ImagePlus className="h-3.5 w-3.5" /> {value ? "Replace" : "Upload photo"}
          </button>
          {canCam && (
            <button type="button" disabled={busy} onClick={() => setCamOpen(true)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-black/10 bg-white px-2.5 py-1.5 text-[12px] font-medium text-ink hover:border-brand/40 disabled:opacity-50">
              <Camera className="h-3.5 w-3.5" /> Take photo
            </button>
          )}
        </div>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = ""; // choosing the same file again must still fire
            if (file) take(() => fromFile(file));
          }}
        />
      </div>
      {(msg || error) && <p className="mt-1 text-[11.5px] text-crimson">{msg || error}</p>}
      {camOpen && (
        <CameraDialog
          onClose={() => setCamOpen(false)}
          onShot={(shot) => {
            setCamOpen(false);
            take(async () => shot);
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------- display
export function FirPhoto({ photoId, alt, className = "" }: { photoId: string; alt: string; className?: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let url: string | null = null;
    let live = true;
    apiFetch(`/fir/photo/${encodeURIComponent(photoId)}`)
      .then((r) => r.blob())
      .then((b) => {
        if (!live) return;
        url = URL.createObjectURL(b);
        setSrc(url);
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [photoId]);
  if (failed || !src)
    return (
      <span className={`grid place-items-center bg-black/[0.04] text-faint ${className}`}>
        {failed ? <UserRound className="h-6 w-6" /> : <Loader2 className="h-5 w-5 animate-spin" />}
      </span>
    );
  // eslint-disable-next-line @next/next/no-img-element -- authenticated blob URL
  return <img src={src} alt={alt} className={`object-cover ${className}`} />;
}
