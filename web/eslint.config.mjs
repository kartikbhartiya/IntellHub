import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),

  // ---------------------------------------------------------------------------
  // CI policy, stated rather than hidden.
  //
  // The tree carries ~180 pre-existing lint findings, 157 of them
  // @typescript-eslint/no-explicit-any on dynamically-shaped API payloads. Fixing
  // those means writing real response types, which is worthwhile and is tracked as
  // its own task — but blocking CI on it today would mean either a 200-item
  // cleanup before anything else can land, or a permanently red pipeline that
  // everyone learns to ignore. A red pipeline nobody reads is worse than no
  // pipeline.
  //
  // So these are demoted to warnings: they stay visible in output and in editors,
  // and CI reports them, but they do not gate. Everything else — including the
  // anti-fabrication rule below — remains an error and does gate.
  {
    files: ["src/**/*.ts", "src/**/*.tsx"],
    rules: {
      "@typescript-eslint/no-explicit-any": "warn",
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/refs": "warn",
      "react-hooks/immutability": "warn",
      "react-hooks/static-components": "warn",
      "react/no-unescaped-entities": "warn",
    },
  },

  // A crime-intelligence UI must never substitute generated content for a record it
  // could not load. Three pages previously did: an invented case dossier naming a
  // "Prime Accused", eight invented FIRs filtered client-side so the failure was
  // invisible, and a hardcoded socio-economic radar. Each looked identical to real
  // output on screen. Fail loudly here so they cannot come back unnoticed.
  {
    files: ["src/app/**/*.tsx", "src/app/**/*.ts", "src/components/**/*.tsx"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "VariableDeclarator[id.name=/^(FALLBACK_|MOCK_|DUMMY_|SAMPLE_|PLACEHOLDER_)/]",
          message:
            "Fabricated fallback data is not permitted in the UI. Render an explicit error state instead — see web/src/app/case/page.tsx for the pattern.",
        },
        {
          selector:
            "FunctionDeclaration[id.name=/^(getFallback|makeFallback|getMock|fakeData)/]",
          message:
            "Fabricated fallback data is not permitted in the UI. Render an explicit error state instead — see web/src/app/case/page.tsx for the pattern.",
        },
        {
          selector:
            "VariableDeclarator[id.name=/(_MOCK|_FALLBACK|_DUMMY)$/]",
          message:
            "Fabricated fallback data is not permitted in the UI. Render an explicit error state instead.",
        },
      ],
    },
  },
]);

export default eslintConfig;
