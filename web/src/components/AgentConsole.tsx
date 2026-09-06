"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useRouter, usePathname } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { Mic, MicOff, Send, Sparkles, X, ArrowRight, Volume2, VolumeX, AudioLines } from "lucide-react";
import { askAgent, AgentResult, AgentSuggestion, ChatTurn, ApiError } from "@/lib/api";

type Lang = { code: string; label: string; native: string };
const LANGS: Lang[] = [
  { code: "en-IN", label: "EN", native: "English" },
  { code: "kn-IN", label: "ಕ", native: "ಕನ್ನಡ" },
];

const SUGGESTIONS = [
  "Show suspects linked to homicide",
  "Show heatmap of Bengaluru Urban",
  "Criminal links of FIR-20345",
  "How many police to deploy?",
];

// Format text for realistic human voice synthesis with natural phonetics & pacing.
// Strips long numeric case IDs so the AI doesn't robotically read "three zero zero zero three…"
function speakable(t: string) {
  return t
    .replace(/[*_`#>|]+/g, " ")
    .replace(/₹/g, " rupees ")
    // Strip long numeric case/FIR IDs (8+ digits) — replace with "this case" or just remove
    .replace(/\b\d{8,}\b/g, "this case")
    // Handle formatted case IDs like FIR-300030013202600004 or Case 300030013202600004
    .replace(/FIR-0*(\d{8,})/gi, "this F I R")
    .replace(/case\s+0*(\d{8,})/gi, "this case")
    // Short FIR numbers are fine to read
    .replace(/FIR-0*(\d{1,7})/gi, " F I R $1 ")
    .replace(/SUS-0*(\d+)/gi, " Suspect $1 ")
    .replace(/KSP/gi, " K S P ")
    .replace(/SCRB/gi, " S C R B ")
    .replace(/MO\b/gi, " Modus Operandi ")
    .replace(/(\d+)%/g, " $1 percent ")
    .replace(/:\s*/g, "... ")
    .replace(/;\s*/g, ", ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 400);
}

function getHumanVoice(langCode: string, preferredVoiceName?: string): SpeechSynthesisVoice | null {
  if (typeof window === "undefined" || !window.speechSynthesis) return null;
  const voices = window.speechSynthesis.getVoices();
  if (!voices || voices.length === 0) return null;

  if (preferredVoiceName) {
    const userChoice = voices.find((v) => v.name === preferredVoiceName);
    if (userChoice) return userChoice;
  }

  const targetLang = (langCode || "en-IN").split("-")[0].toLowerCase(); // 'en', 'hi', 'kn'

  // Known Female Voice identifiers across Windows, Mac, iOS, Android, Chrome, Edge
  const femaleVoiceNames = [
    "aria", "jenny", "zira", "heera", "neerja", "samantha", "siri",
    "karen", "victoria", "fiona", "moira", "tessa", "veena", "sunhi",
    "xiaoxiao", "google uk english female", "google us english", "google हिन्दी",
    "female", "girl", "woman", "natural female"
  ];
  const maleVoiceNames = ["david", "mark", "george", "ravi", "prabhat", "guy", "male", "boy", "man"];

  const isFemaleVoice = (v: SpeechSynthesisVoice) => {
    const name = v.name.toLowerCase();
    if (femaleVoiceNames.some((k) => name.includes(k))) return true;
    if (maleVoiceNames.some((k) => name.includes(k))) return false;
    return !name.includes("david") && !name.includes("mark");
  };

  // 1. Priority 1: Neural/Natural FEMALE voice matching language
  const neuralFemaleLang = voices.find((v) => {
    const l = v.lang.toLowerCase();
    const n = v.name.toLowerCase();
    const isLang = l.startsWith(targetLang) || (targetLang === "en" && l.startsWith("en"));
    const isNeural = n.includes("natural") || n.includes("neural") || n.includes("online") || n.includes("enhanced");
    return isLang && isFemaleVoice(v) && isNeural;
  });
  if (neuralFemaleLang) return neuralFemaleLang;

  // 2. Priority 2: ANY Neural/Natural FEMALE voice on the system
  const neuralFemaleGlobal = voices.find((v) => {
    const n = v.name.toLowerCase();
    const isNeural = n.includes("natural") || n.includes("neural") || n.includes("online") || n.includes("enhanced");
    return isFemaleVoice(v) && isNeural;
  });
  if (neuralFemaleGlobal) return neuralFemaleGlobal;

  // 3. Priority 3: ANY FEMALE voice matching target language (e.g. Zira, Heera, Google Female)
  const femaleLang = voices.find((v) => {
    const l = v.lang.toLowerCase();
    return (l.startsWith(targetLang) || (targetLang === "en" && l.startsWith("en"))) && isFemaleVoice(v);
  });
  if (femaleLang) return femaleLang;

  // 4. Priority 4: ANY FEMALE voice on the system (e.g. Zira, Samantha, Siri, Google Female, Heera)
  const anyFemaleSystem = voices.find((v) => isFemaleVoice(v));
  if (anyFemaleSystem) return anyFemaleSystem;

  // 5. Absolute Fallback: Non-David voice
  const nonDavid = voices.find((v) => !v.name.toLowerCase().includes("david"));
  if (nonDavid) return nonDavid;

  return voices[0] || null;
}

/* Animated listening orb — pulses while listening, spins while thinking, glows while speaking. */
function VoiceOrb({ state }: { state: "idle" | "listening" | "thinking" | "speaking" }) {
  const active = state === "listening" || state === "speaking";
  const ring = state === "speaking" ? "#5b9bd5" : "#e7b43c";
  return (
    <div className="relative grid h-24 w-24 place-items-center">
      {/* expanding rings */}
      {active &&
        [0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className="absolute rounded-full"
            style={{ border: `1px solid ${ring}`, width: 56, height: 56 }}
            initial={{ scale: 1, opacity: 0.55 }}
            animate={{ scale: 2.1, opacity: 0 }}
            transition={{ duration: 2.2, repeat: Infinity, ease: "easeOut", delay: i * 0.7 }}
          />
        ))}
      {/* thinking ring */}
      {state === "thinking" && (
        <motion.span
          className="absolute h-[60px] w-[60px] rounded-full border-2 border-transparent"
          style={{ borderTopColor: "#e7b43c", borderRightColor: "rgba(231,180,60,0.35)" }}
          animate={{ rotate: 360 }}
          transition={{ duration: 0.9, repeat: Infinity, ease: "linear" }}
        />
      )}
      {/* core */}
      <motion.div
        className="relative grid h-14 w-14 place-items-center rounded-full"
        style={{
          background:
            state === "speaking"
              ? "radial-gradient(circle at 30% 30%, #7fb4e0, #4a86c2)"
              : "radial-gradient(circle at 30% 30%, #f5d486, #d29a1f)",
          boxShadow: `0 0 26px -4px ${ring}`,
        }}
        animate={active ? { scale: [1, 1.08, 1] } : { scale: 1 }}
        transition={{ duration: 1.4, repeat: active ? Infinity : 0, ease: "easeInOut" }}
      >
        {state === "speaking" ? (
          <AudioLines className="h-6 w-6 text-black/90" />
        ) : (
          <Mic className="h-6 w-6 text-black/80" />
        )}
      </motion.div>
    </div>
  );
}

export default function AgentConsole() {
  const router = useRouter();
  const pathname = usePathname() || "/";

  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [lang, setLang] = useState<Lang>(LANGS[0]);
  const [reply, setReply] = useState<{ text: string; action: string } | null>(null);
  const [suggestions, setSuggestions] = useState<AgentSuggestion[]>([]);
  const [speechOK, setSpeechOK] = useState(false);

  // Voice-mode state & Realistic Neural TTS
  const [voiceMode, setVoiceMode] = useState(false);
  const [tts, setTts] = useState(true);
  const [speaking, setSpeaking] = useState(false);
  const [interim, setInterim] = useState("");
  const [lastCommand, setLastCommand] = useState("");
  const [voiceNote, setVoiceNote] = useState("");
  const [availableVoices, setAvailableVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [selectedVoiceName, setSelectedVoiceName] = useState<string>("");

  const inputRef = useRef<HTMLInputElement>(null);
  const recogRef = useRef<any>(null);
  const historyRef = useRef<ChatTurn[]>([]);

  // Load available SpeechSynthesis voices dynamically and bind best female voice
  useEffect(() => {
    if (typeof window === "undefined" || !window.speechSynthesis) return;
    const updateVoices = () => {
      const vs = window.speechSynthesis.getVoices();
      setAvailableVoices(vs);
      if (vs.length > 0) {
        const prefix = (lang.code || "en").split("-")[0].toLowerCase();
        const currentVoice = vs.find((v) => v.name === selectedVoiceNameRef.current);
        const matchesLang = currentVoice && currentVoice.lang.toLowerCase().startsWith(prefix);
        
        if (!selectedVoiceNameRef.current || !matchesLang) {
          const female = getHumanVoice(lang.code);
          if (female) {
            setSelectedVoiceName(female.name);
          }
        }
      }
    };
    updateVoices();
    window.speechSynthesis.onvoiceschanged = updateVoices;
    return () => {
      if (typeof window !== "undefined" && window.speechSynthesis) {
        window.speechSynthesis.onvoiceschanged = null;
      }
    };
  }, [lang.code]);

  // Refs mirror state so the long-lived recognition/TTS callbacks never read stale values.
  const pathRef = useRef(pathname);
  pathRef.current = pathname;
  const langRef = useRef(lang);
  langRef.current = lang;
  const voiceRef = useRef(false);
  const ttsRef = useRef(true);
  ttsRef.current = tts;
  const busyRef = useRef(false);
  busyRef.current = busy;
  const selectedVoiceNameRef = useRef(selectedVoiceName);
  selectedVoiceNameRef.current = selectedVoiceName;
  const speakingRef = useRef(false);
  // Timestamp when TTS finished — used to create a cooldown window where mic
  // results are discarded so the AI never hears its own voice from the speakers.
  const ttsEndedAt = useRef(0);

  useEffect(() => {
    const SR =
      (typeof window !== "undefined" &&
        ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition)) ||
      null;
    setSpeechOK(!!SR);
  }, []);

  const route = useCallback(
    (r: AgentResult) => {
      const p = r.params || {};
      const qs = new URLSearchParams();
      switch (r.action) {
        case "open_case":
          if (p.case_id) router.push(`/case?id=${encodeURIComponent(p.case_id)}`);
          break;
        case "open_suspect":
          if (p.suspect_id) router.push(`/suspect?id=${encodeURIComponent(p.suspect_id)}`);
          break;
        case "show_heatmap":
          if (p.district) qs.set("district", p.district);
          if (p.time_of_day) qs.set("time_of_day", p.time_of_day);
          if (p.case_id) qs.set("case_id", p.case_id);
          router.push(`/map${qs.toString() ? `?${qs}` : ""}`);
          break;
        case "show_network":
          if (p.suspect_id) qs.set("suspect_id", p.suspect_id);
          if (p.case_id) qs.set("case_id", p.case_id);
          const netTarget = p.q || p.query || p.target;
          if (netTarget) qs.set("q", netTarget);
          router.push(`/network${qs.toString() ? `?${qs}` : ""}`);
          break;
        case "show_dispatch":
          if (p.district) qs.set("district", p.district);
          router.push(`/dispatch${qs.toString() ? `?${qs}` : ""}`);
          break;
        case "show_anomalies":
          router.push("/?view=anomalies#anomalies");
          break;
        case "show_analytics":
          router.push("/analytics");
          break;
        // The dedicated registry page was removed from the navigation; Analytics
        // carries the registry summary, so send the copilot there rather than to a
        // route that no longer exists. An action the client cannot honour is worse
        // than no action.
        case "show_models":
          router.push("/analytics");
          break;
        case "recent_cases":
          if (p.district) qs.set("district", p.district);
          if (p.crime_type) qs.set("crime_type", p.crime_type);
          if (p.q) qs.set("q", p.q);
          router.push(`/cases${qs.toString() ? `?${qs}` : ""}`);
          break;
        case "show_registry":
        case "show_suspects": {
          const isSuspectTab = p.kind === "criminals" || p.kind === "suspects" || (r.action as string) === "show_suspects";
          const tab = isSuspectTab ? "criminals" : (p.kind || "criminals");
          const term = p.q || p.query || p.target || "";
          qs.set("tab", tab);
          if (p.crime_type || term.toLowerCase().includes("homicide") || term.toLowerCase().includes("murder")) {
            qs.set("crime_type", p.crime_type || "Homicide");
          }
          if (p.repeat_only || term.toLowerCase().includes("repeat") || term.toLowerCase().includes("prior") || term.toLowerCase().includes("recidiv")) {
            qs.set("repeat_only", "true");
          }
          if (term && !term.toLowerCase().includes("homicide") && !term.toLowerCase().includes("murder")) {
            qs.set("q", term);
          }
          router.push(`/intel?${qs}`);
          break;
        }
        case "open_copilot":
          router.push("/analytics");
          break;
      }
    },
    [router]
  );

  // --- Text-to-speech: pause the mic while speaking so it never hears itself. ---
  const beginRecognitionRef = useRef<() => void>(() => {});

  const ttsWatchdog = useRef<any>(null);
  // Chrome/Edge TTS keep-alive: Chrome pauses SpeechSynthesis after ~15 s of
  // continuous playback. Calling resume() on a short interval prevents this.
  const ttsKeepAlive = useRef<any>(null);

  const speak = useCallback((text: string) => {
    const synth = typeof window !== "undefined" ? window.speechSynthesis : null;
    if (!synth || !ttsRef.current) return;
    try {
      speakingRef.current = true;
      setSpeaking(true);
      if (recogRef.current) {
        try { recogRef.current.stop(); } catch {}
      }
      try { synth.cancel(); } catch {}
      if (synth.paused) {
        try { synth.resume(); } catch {}
      }

      const clean = speakable(text);
      if (!clean) {
        speakingRef.current = false;
        setSpeaking(false);
        return;
      }

      const isEnglishText = /[a-zA-Z]/.test(clean);
      const targetLang = isEnglishText ? "en-IN" : (langRef.current?.code || "en-IN");

      const u = new SpeechSynthesisUtterance(clean);
      u.rate = 0.96;
      u.pitch = 1.08;
      u.volume = 1.0;

      const voiceName = selectedVoiceNameRef.current || selectedVoiceName;
      const bestVoice = getHumanVoice(langRef.current?.code || "en-IN", voiceName);
      if (bestVoice) {
        try {
          u.voice = bestVoice;
        } catch {}
      }
      
      // Ensure u.lang matches the voice's language to prevent SpeechSynthesis errors/crashes
      u.lang = bestVoice ? bestVoice.lang : targetLang;

      let finished = false;
      const done = () => {
        if (finished) return;
        finished = true;
        clearTimeout(ttsWatchdog.current);
        clearInterval(ttsKeepAlive.current);
        speakingRef.current = false;
        setSpeaking(false);
        // Record when TTS ended — onresult will discard anything captured
        // within the cooldown window so the mic never echoes the speaker.
        ttsEndedAt.current = Date.now();
        // Long delay (1s) before restarting mic so the laptop speaker audio
        // fully dissipates and the mic doesn't pick up the AI's own voice.
        if (voiceRef.current) {
          setTimeout(() => beginRecognitionRef.current(), 1000);
        }
      };

      u.onend = done;
      u.onerror = (err: any) => {
        const errType = String(err?.error || err || "").toLowerCase();
        if (!finished) {
          finished = true;
          clearTimeout(ttsWatchdog.current);
          clearInterval(ttsKeepAlive.current);
          // If the user closed/canceled speech synthesis or stopped voice mode, DO NOT trigger fallback voice.
          if (errType.includes("canceled") || errType.includes("interrupted") || !speakingRef.current || !voiceRef.current) {
            speakingRef.current = false;
            setSpeaking(false);
            return;
          }
          try {
            try { synth.cancel(); } catch {}
            // Reliable Fallback: Create a clean utterance WITHOUT forcing a custom voice,
            // letting the browser engine use its robust default voice for the language.
            const fb = new SpeechSynthesisUtterance(clean);
            fb.rate = 0.96;
            fb.pitch = 1.0;
            fb.volume = 1.0;
            fb.lang = targetLang;
            fb.onend = () => {
              speakingRef.current = false;
              setSpeaking(false);
              ttsEndedAt.current = Date.now();
              if (voiceRef.current) setTimeout(() => beginRecognitionRef.current(), 1000);
            };
            fb.onerror = () => {
              speakingRef.current = false;
              setSpeaking(false);
              ttsEndedAt.current = Date.now();
              if (voiceRef.current) setTimeout(() => beginRecognitionRef.current(), 1000);
            };
            setTimeout(() => {
              try {
                if (speakingRef.current && voiceRef.current) synth.speak(fb);
                else {
                  speakingRef.current = false;
                  setSpeaking(false);
                }
              } catch {
                done();
              }
            }, 60);
            return;
          } catch {}
        }
        done();
      };

      setTimeout(() => {
        try {
          if (synth.paused) synth.resume();
          synth.speak(u);
          // Chrome keep-alive: nudge synth every 5s to prevent the 15s pause bug
          clearInterval(ttsKeepAlive.current);
          ttsKeepAlive.current = setInterval(() => {
            if (!synth.speaking) {
              clearInterval(ttsKeepAlive.current);
              return;
            }
            if (synth.paused) synth.resume();
          }, 5000);
        } catch {
          done();
        }
      }, 50);

      clearTimeout(ttsWatchdog.current);
      // More generous watchdog: 2.5s base + 85ms per char, max 25s
      ttsWatchdog.current = setTimeout(done, Math.min(25000, 2500 + clean.length * 85));
    } catch {
      speakingRef.current = false;
      setSpeaking(false);
    }
  }, [selectedVoiceName]);

  const submit = useCallback(
    async (text: string) => {
      const q = text.trim();
      if (!q || busyRef.current) return;
      busyRef.current = true; // set synchronously so rapid speech-finals can't double-submit
      setBusy(true);
      setInput("");
      setInterim("");
      setLastCommand(q);
      setReply(null);
      setSuggestions([]);
      try {
        // Unlock browser SpeechSynthesis audio context if needed
        if (typeof window !== "undefined" && window.speechSynthesis) {
          if (window.speechSynthesis.paused) window.speechSynthesis.resume();
        }
        // Current-page entity so the agent resolves "this case / that suspect / open its suspect".
        let context: { case_id?: string; suspect_id?: string } | undefined;
        if (typeof window !== "undefined") {
          const sp = new URLSearchParams(window.location.search);
          const id = sp.get("id") || undefined;
          if (pathRef.current.startsWith("/case") && id) context = { case_id: id };
          else if (pathRef.current.startsWith("/suspect") && id) context = { suspect_id: id };
        }
        let r: AgentResult;
        try {
          r = await askAgent(q, historyRef.current.slice(-8), langRef.current.code.split("-")[0], pathRef.current, context);
        } catch (err: any) {
          if (err instanceof ApiError && err.isForbidden) {
            r = {
              action: "answer",
              reply: "Access denied. Please log in with investigator privileges to use the AI assistant.",
              params: {},
            };
          } else {
            // Client-side intelligent fallback if backend is offline
            r = {
              action: "answer",
              reply: "Could not reach the intelligence core. Executing local navigation.",
              params: {},
            };
          }
        }

        // The backend's routing decision is authoritative. A block here used to
        // discard r.action, r.params AND r.reply for any query containing
        // "suspect" / "offender" / "rowdy" / "criminal", substituting a registry
        // navigation and a hand-written sentence. That silently overrode the server
        // for a whole class of queries — including its grounding check — so the
        // reply shown was not the reply the server had verified.

        historyRef.current.push({ role: "user", content: q });
        historyRef.current.push({ role: "assistant", content: r.reply });
        setReply({ text: r.reply, action: r.action });
        setSuggestions(r.suggestions?.slice(0, 3) ?? []);
        route(r);
        if (ttsRef.current) (r.grounding && r.grounding.grounded === false
            // An unverified answer is not read aloud. Synthesised speech lends an
            // invented statement more authority, not less, and the listener has no
            // way to see the caution text.
            ? speak("This answer could not be verified against the case records. Please read it on screen.")
            : speak(r.reply));
      } catch {
        const msg = "Could not process request. Please try again.";
        setReply({ text: msg, action: "answer" });
        if (ttsRef.current) speak(msg);
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [route, speak]
  );

  // --- Continuous recognition: ONE persistent instance (the "always listening" core). ---
  // Creating a fresh instance on every restart multiplied handlers and made the mic flicker
  // open/closed, recognising nothing. We keep a single instance and just re-start() it.
  const runningRef = useRef(false);
  const restartTimer = useRef<any>(null);
  const lastStartAt = useRef(0);
  const listeningStartedAt = useRef(0);
  const quickEndsRef = useRef(0);
  const safeStartRef = useRef<() => void>(() => {});
  const submitRef = useRef(submit);
  submitRef.current = submit;

  const ensureRecog = useCallback(() => {
    if (recogRef.current) return recogRef.current;
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) return null;
    let recog: any;
    try {
      recog = new SR();
    } catch {
      return null;
    }
    recog.continuous = true;
    recog.interimResults = true;
    recog.maxAlternatives = 1;

    recog.onstart = () => {
      runningRef.current = true;
      listeningStartedAt.current = Date.now();
      lastStartAt.current = Date.now();
      setListening(true);
      setVoiceNote("");
    };
    recog.onend = () => {
      runningRef.current = false;
      setListening(false);
      if (!voiceRef.current || speakingRef.current) return;

      // Don't restart if we're inside the post-TTS cooldown window —
      // the done() callback in speak() will handle the restart after the delay.
      const msSinceTTS = ttsEndedAt.current > 0 ? Date.now() - ttsEndedAt.current : Infinity;
      if (msSinceTTS < 1200) return;
      
      const listeningDur = listeningStartedAt.current > 0 ? Date.now() - listeningStartedAt.current : 0;
      listeningStartedAt.current = 0; // Reset for next session

      if (listeningDur < 1500) {
        // Died almost instantly or very quickly (mic busy / permission blocked / OS hiccup / rapid error). 
        // Back off, and after a streak give up with guidance so it can NEVER flicker open/closed forever.
        quickEndsRef.current += 1;
        if (quickEndsRef.current >= 5) {
          voiceRef.current = false;
          setVoiceMode(false);
          setVoiceNote(
            "Voice couldn't hold the microphone. Check this site's mic permission (icon in the address bar) " +
              "and close any app using the mic (Zoom, Meet, Teams), then turn voice on again. Chrome or Edge works best."
          );
          return;
        }
        clearTimeout(restartTimer.current);
        restartTimer.current = setTimeout(() => safeStartRef.current(), 1000); // 1s backoff to prevent fast cycling
        return;
      }
      // Normal end (Chrome's silence timeout) → restart the SAME instance after a brief delay
      // to let the browser release the device, ensuring the restart is clean and lastStartAt is updated.
      quickEndsRef.current = 0;
      clearTimeout(restartTimer.current);
      restartTimer.current = setTimeout(() => safeStartRef.current(), 150);
    };
    recog.onerror = (e: any) => {
      if (e?.error === "not-allowed" || e?.error === "service-not-allowed") {
        voiceRef.current = false;
        setVoiceMode(false);
        setVoiceNote("Microphone blocked. Click the mic/lock icon in the address bar, allow access, then turn voice on again.");
      } else if (e?.error === "audio-capture") {
        voiceRef.current = false;
        setVoiceMode(false);
        setVoiceNote("No microphone found. Plug one in and try again.");
      }
      // 'no-speech' / 'aborted' / 'network' are transient — onend handles the restart.
    };
    recog.onresult = (ev: any) => {
      quickEndsRef.current = 0; // Got a result, microphone is capturing audio successfully

      // Discard ALL results during TTS and within 1.2 s after TTS ends.
      // This prevents the mic from picking up the laptop speaker playing
      // the AI's own voice and treating it as a new user command.
      if (speakingRef.current) return;
      const msSinceTTS = ttsEndedAt.current > 0 ? Date.now() - ttsEndedAt.current : Infinity;
      if (msSinceTTS < 1200) return;

      let interimText = "";
      let finalText = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const res = ev.results[i];
        if (res.isFinal) finalText += res[0].transcript;
        else interimText += res[0].transcript;
      }
      if (interimText) setInterim(interimText);
      if (finalText.trim().length > 1 && !busyRef.current && !speakingRef.current) {
        setInterim("");
        submitRef.current(finalText);
      }
    };
    recogRef.current = recog;
    return recog;
  }, []);

  const safeStart = useCallback(() => {
    if (!voiceRef.current || speakingRef.current || runningRef.current) return;
    const recog = ensureRecog();
    if (!recog) return;
    recog.lang = langRef.current.code;
    lastStartAt.current = Date.now();
    try {
      recog.start();
      runningRef.current = true;
    } catch (e: any) {
      // "already started" → it IS running; anything else, onend will retry.
      if (e?.name === "InvalidStateError") runningRef.current = true;
    }
  }, [ensureRecog]);
  safeStartRef.current = safeStart;
  beginRecognitionRef.current = safeStart;

  const stopVoice = useCallback(() => {
    voiceRef.current = false;
    speakingRef.current = false;
    setVoiceMode(false);
    setSpeaking(false);
    setListening(false);
    setInterim("");
    clearTimeout(restartTimer.current);
    clearInterval(ttsKeepAlive.current);
    try {
      recogRef.current?.stop();
    } catch {
      /* ignore */
    }
    runningRef.current = false;
    if (typeof window !== "undefined") {
      try { window.speechSynthesis?.cancel(); } catch {}
    }
  }, []);

  const startVoice = useCallback(() => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) {
      setVoiceNote("Voice needs a Chromium browser (Chrome/Edge) over HTTPS or localhost.");
      return;
    }
    // Clear any stuck TTS/failure state from a previous session before starting fresh.
    if (typeof window !== "undefined") {
      window.speechSynthesis?.cancel();
      // Unlock audio context on Safari/Chrome by playing silent audio during user gesture
      const unlock = new SpeechSynthesisUtterance("");
      unlock.volume = 0;
      window.speechSynthesis?.speak(unlock);
    }
    speakingRef.current = false;
    setSpeaking(false);
    quickEndsRef.current = 0;
    listeningStartedAt.current = 0;
    voiceRef.current = true;
    setVoiceMode(true);
    // Force TTS on in voice mode — the agent must always speak back
    setTts(true);
    ttsRef.current = true;
    setOpen(true);
    setReply(null);
    setSuggestions([]);
    safeStart();
  }, [safeStart]);

  const toggleVoice = useCallback(() => {
    if (voiceRef.current) stopVoice();
    else startVoice();
  }, [startVoice, stopVoice]);

  // Restart recognition with the new language when it changes mid-session.
  useEffect(() => {
    if (voiceRef.current && !speakingRef.current && runningRef.current) {
      try {
        recogRef.current?.stop(); // onend relaunches via safeStart with the new lang
      } catch {
        /* ignore */
      }
    }
  }, [lang]);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      voiceRef.current = false;
      clearTimeout(restartTimer.current);
      clearInterval(ttsKeepAlive.current);
      try {
        recogRef.current?.stop();
      } catch {
        /* ignore */
      }
      if (typeof window !== "undefined") window.speechSynthesis?.cancel();
    };
  }, []);

  // ⌘K focuses the text console.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(true);
        setTimeout(() => inputRef.current?.focus(), 60);
      }
      if (e.key === "Escape") {
        if (voiceRef.current) stopVoice();
        else setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [stopVoice]);

  const orbState: "idle" | "listening" | "thinking" | "speaking" = speaking
    ? "speaking"
    : busy
    ? "thinking"
    : listening
    ? "listening"
    : "idle";

  const stateLabel = speaking
    ? "Speaking…"
    : busy
    ? "Thinking…"
    : listening
    ? "Listening"
    : voiceMode
    ? "Starting mic…"
    : "";

  return (
    <div id="agent-console" className="fixed inset-x-0 bottom-0 z-40 flex justify-center px-4 pb-5">
      <div className="w-full max-w-2xl">
        {/* ---------------- Premium Voice HUD ---------------- */}
        <AnimatePresence>
          {voiceMode && (
            <motion.div
              key="voice-hud"
              initial={{ opacity: 0, y: 18, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 12, scale: 0.98 }}
              transition={{ duration: 0.45, ease: [0.32, 0.72, 0, 1] }}
              className="mb-3 overflow-hidden rounded-[1.8rem] border border-black/12 bg-[#ffffff]/97 shadow-[0_30px_80px_-24px_rgba(17,20,32,0.16)] backdrop-blur-2xl"
            >
              {/* animated top hairline */}
              <div
                className={`h-[2px] w-full ${
                  orbState === "speaking"
                    ? "bg-gradient-to-r from-transparent via-azure to-transparent"
                    : "bg-gradient-to-r from-transparent via-brand to-transparent"
                } ${orbState === "listening" || orbState === "speaking" ? "shimmer" : ""}`}
              />
              <div className="p-5">
                {/* header row */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="relative flex h-2 w-2">
                      <span
                        className={`absolute inline-flex h-full w-full rounded-full ${
                          listening ? "animate-ping bg-brand" : "bg-faint"
                        } opacity-75`}
                      />
                      <span className={`relative inline-flex h-2 w-2 rounded-full ${listening ? "bg-brand" : "bg-faint"}`} />
                    </span>
                    <span className="text-[11px] font-semibold uppercase tracking-[0.2em] text-brand">Kavya Voice AI</span>
                    {stateLabel && <span className="text-[11px] text-faint">· {stateLabel}</span>}
                  </div>
                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => setTts((v) => !v)}
                      title={tts ? "Voice replies on" : "Voice replies off"}
                      className={`grid h-8 w-8 place-items-center rounded-full transition-colors ${
                        tts ? "bg-brand/15 text-brand" : "bg-black/[0.04] text-faint hover:text-ink"
                      }`}
                    >
                      {tts ? <Volume2 className="h-4 w-4" /> : <VolumeX className="h-4 w-4" />}
                    </button>
                    {availableVoices.length > 0 && (
                      <select
                        value={selectedVoiceName}
                        onChange={(e) => setSelectedVoiceName(e.target.value)}
                        className="hidden max-w-[150px] rounded-full border border-black/10 bg-black/[0.04] px-2.5 py-1 text-[11px] font-semibold text-ink outline-none transition-all hover:bg-black/[0.08] sm:block cursor-pointer"
                        title="Select Human Neural Voice"
                      >
                        <option value="">👩 Realistic Female (Auto)</option>
                        {availableVoices
                          .filter((v) => {
                            const prefix = (lang.code || "en").split("-")[0].toLowerCase();
                            return v.lang.toLowerCase().startsWith(prefix);
                          })
                          .map((v) => (
                            <option key={v.name} value={v.name}>
                              {v.name.replace(/Microsoft|Google|Apple|Online|\(Natural\)/gi, "").trim()}
                            </option>
                          ))}
                      </select>
                    )}
                    <div className="hidden items-center rounded-full bg-black/[0.04] p-0.5 sm:flex">
                      {LANGS.map((l) => (
                        <button
                          key={l.code}
                          onClick={() => setLang(l)}
                          title={l.native}
                          className={`h-7 min-w-7 rounded-full px-2 text-[11.5px] font-semibold transition-all ${
                            lang.code === l.code ? "bg-brand text-black" : "text-muted hover:text-ink"
                          }`}
                        >
                          {l.label}
                        </button>
                      ))}
                    </div>
                    <button
                      onClick={stopVoice}
                      title="Turn voice off (Esc)"
                      className="grid h-8 w-8 place-items-center rounded-full bg-black/[0.04] text-faint transition-colors hover:bg-crimson/15 hover:text-crimson"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {/* orb */}
                <div className="mt-3 flex justify-center">
                  <VoiceOrb state={orbState} />
                </div>

                {/* live transcript */}
                <div className="mt-2 min-h-[2.5rem] px-2 text-center">
                  {interim ? (
                    <p className="text-[17px] font-medium leading-snug text-ink">
                      {interim}
                      <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-brand align-middle" />
                    </p>
                  ) : lastCommand && (busy || speaking || reply) ? (
                    <p className="text-[15px] leading-snug text-muted">“{lastCommand}”</p>
                  ) : (
                    <p className="text-[15px] text-faint">
                      {listening ? "Listening… just speak your command" : "Warming up the microphone…"}
                    </p>
                  )}
                </div>

                {/* reply */}
                <AnimatePresence mode="wait">
                  {reply && !busy && (
                    <motion.div
                      key={reply.text}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      className="mt-3 flex items-start gap-2.5 rounded-2xl border border-black/[0.07] bg-black/[0.03] p-3.5"
                    >
                      <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-brand/15 text-brand">
                        <Sparkles className="h-3.5 w-3.5" />
                      </span>
                      <p className="text-[13.5px] leading-relaxed text-ink/90">{reply.text}</p>
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* suggestion chips */}
                {suggestions.length > 0 && !busy && (
                  <div className="mt-3 flex flex-wrap justify-center gap-1.5">
                    {suggestions.map((s) => (
                      <button
                        key={s.query}
                        onClick={() => submit(s.query)}
                        className="group inline-flex items-center gap-1 rounded-full border border-brand/25 bg-brand/[0.06] px-3 py-1.5 text-[11.5px] font-medium text-brand/90 transition-all hover:border-brand/50 hover:bg-brand/12 hover:text-brand active:scale-95"
                      >
                        {s.label}
                        <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ---------------- Reply ribbon (text mode) ---------------- */}
        <AnimatePresence>
          {reply && !voiceMode && (
            <motion.div
              initial={{ opacity: 0, y: 12, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 8 }}
              transition={{ duration: 0.4, ease: [0.32, 0.72, 0, 1] }}
              className="mb-3 flex items-start gap-3 rounded-2xl border border-black/10 bg-[#ffffff]/97 p-4 shadow-[0_24px_60px_-20px_rgba(17,20,32,0.16)] backdrop-blur-xl"
            >
              <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-brand/15 text-brand">
                <Sparkles className="h-4 w-4" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[13.5px] leading-relaxed text-ink/90">{reply.text}</p>
                {suggestions.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {suggestions.map((s) => (
                      <button
                        key={s.query}
                        onClick={() => submit(s.query)}
                        disabled={busy}
                        className="group inline-flex items-center gap-1 rounded-full border border-brand/25 bg-brand/[0.06] px-3 py-1.5 text-[11.5px] font-medium text-brand/90 transition-all hover:border-brand/50 hover:bg-brand/12 hover:text-brand active:scale-95 disabled:opacity-40"
                      >
                        {s.label}
                        <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <button
                type="button"
                aria-label="Dismiss assistant reply"
                onClick={() => {
                  setReply(null);
                  setSuggestions([]);
                }}
                className="text-faint transition-colors hover:text-ink"
              >
                <X className="h-4 w-4" />
              </button>
            </motion.div>
          )}
        </AnimatePresence>

        {voiceNote && !voiceMode && (
          <div className="mb-3 rounded-2xl border border-crimson/25 bg-crimson/[0.06] px-4 py-2.5 text-center text-[12.5px] text-crimson">
            {voiceNote}
          </div>
        )}

        {/* ---------------- Console dock ---------------- */}
        <motion.div
          layout
          initial={{ y: 28, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          transition={{ duration: 0.7, ease: [0.32, 0.72, 0, 1], delay: 0.15 }}
          className="rounded-[1.6rem] border border-black/10 bg-[#ffffff]/97 p-2 shadow-[0_28px_70px_-20px_rgba(17,20,32,0.16)] backdrop-blur-xl"
        >
          <AnimatePresence>
            {open && !voiceMode && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.3, ease: [0.32, 0.72, 0, 1] }}
                className="overflow-hidden"
              >
                <div className="flex flex-wrap gap-2 px-2 pb-2.5 pt-1.5">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => submit(s)}
                      className="rounded-full border border-black/10 bg-black/[0.03] px-3 py-1.5 text-[11.5px] text-muted transition-all hover:border-brand/40 hover:text-ink active:scale-95"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit(input);
            }}
            className="flex items-center gap-2"
          >
            {/* Voice toggle */}
            <button
              type="button"
              onClick={toggleVoice}
              disabled={!speechOK}
              aria-pressed={voiceMode}
              aria-label={speechOK ? (voiceMode ? "Turn voice off" : "Turn voice on") : "Voice not supported in this browser"}
              title={speechOK ? (voiceMode ? "Turn voice off" : "Turn voice on — always listening") : "Voice not supported in this browser"}
              className={`relative grid h-11 w-11 shrink-0 place-items-center rounded-full transition-all duration-300 active:scale-95 ${
                voiceMode
                  ? "bg-gradient-to-b from-brand-soft to-brand text-black pulse-ring"
                  : "bg-black/[0.04] text-muted hover:bg-black/[0.08] hover:text-ink disabled:opacity-40"
              }`}
            >
              {speechOK ? <Mic className="h-[18px] w-[18px]" /> : <MicOff className="h-[18px] w-[18px]" />}
            </button>

            <input
              ref={inputRef}
              value={input}
              onFocus={() => setOpen(true)}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                voiceMode ? "Kavya is listening — speak your command or type here…" : "Ask Kavya AI — “open FIR-20345”, “ai suspect matching”, “show heatmap”…"
              }
              className="min-w-0 flex-1 bg-transparent px-2 text-[14px] text-ink placeholder:text-faint focus:outline-none"
            />

            {/* Language toggle (text mode) */}
            {!voiceMode && (
              <div className="hidden items-center rounded-full bg-black/[0.04] p-0.5 sm:flex">
                {LANGS.map((l) => (
                  <button
                    key={l.code}
                    type="button"
                    onClick={() => setLang(l)}
                    aria-pressed={lang.code === l.code}
                    aria-label={`Switch language to ${l.native}`}
                    title={l.native}
                    className={`h-8 min-w-8 rounded-full px-2 text-[12px] font-semibold transition-all ${
                      lang.code === l.code ? "bg-brand text-black" : "text-muted hover:text-ink"
                    }`}
                  >
                    {l.label}
                  </button>
                ))}
              </div>
            )}

            <button
              type="submit"
              aria-label="Send query to the assistant"
              disabled={!input.trim() || busy}
              className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-gradient-to-b from-brand-soft to-brand text-black shadow-[0_4px_18px_-4px_rgba(231,180,60,0.7)] transition-all hover:brightness-105 active:scale-95 disabled:opacity-40 disabled:shadow-none"
            >
              {busy ? (
                <span className="flex items-end gap-0.5">
                  <span className="eq-bar h-3 w-0.5 bg-black/80" style={{ animationDelay: "0ms" }} />
                  <span className="eq-bar h-3 w-0.5 bg-black/80" style={{ animationDelay: "150ms" }} />
                  <span className="eq-bar h-3 w-0.5 bg-black/80" style={{ animationDelay: "300ms" }} />
                </span>
              ) : (
                <Send className="h-[17px] w-[17px]" />
              )}
            </button>
          </form>
        </motion.div>

        <p className="mt-2 hidden text-center text-[10.5px] tracking-wide text-faint sm:block">
          <kbd className="rounded bg-black/5 px-1.5 py-0.5 font-sans">⌘K</kbd> to command ·{" "}
          {speechOK ? (
            <>
              <Mic className="inline h-3 w-3" /> tap to talk hands-free
            </>
          ) : (
            "Llama-3.3 70B · EN / ಕನ್ನಡ"
          )}
        </p>
      </div>
    </div>
  );
}
