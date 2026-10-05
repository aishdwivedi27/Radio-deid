import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";

// File-size limits from CLAUDE.md (File size): hard cap 1,000 lines per file, 80 per function.
export const sizeRules = {
  "max-lines": ["error", { max: 1000, skipBlankLines: false, skipComments: false }],
  "max-lines-per-function": ["error", { max: 80, skipBlankLines: true, skipComments: true }],
};

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  {
    files: ["**/*.{ts,tsx,js}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { ecmaVersion: 2022, globals: { ...globals.browser, ...globals.node } },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...sizeRules,
    },
  },
);
