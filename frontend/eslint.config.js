// ESLint flat config for Vue 3 + TypeScript
// Docs: https://eslint.org/docs/latest/use/configure/configuration-files-new
import js from '@eslint/js'
import ts from 'typescript-eslint'
import tsParser from '@typescript-eslint/parser'
import vue from 'eslint-plugin-vue'
import vueParser from 'vue-eslint-parser'

export default [
  // Ignores (flat config replaces .eslintignore)
  { ignores: ['dist', 'node_modules', 'coverage', '**/*.config.cjs'] },

  // Base JS recommended
  js.configs.recommended,

  // Vue 3 recommended (SFC parsing, template rules)
  ...vue.configs['flat/recommended'],

  // Config files and ESM globals
  {
    files: ['**/*.cjs', 'vitest.config.js'],
    languageOptions: {
      globals: {
        module: 'readonly',
        require: 'readonly',
        __dirname: 'readonly'
      }
    }
  },

  // TypeScript recommended (no type-aware rules to keep it light)
  ...ts.configs.recommended,

  // Project-specific tweaks
  {
    rules: {
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      'no-debugger': 'warn',
      'no-empty': 'off',
      '@typescript-eslint/no-empty-object-type': 'off',
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      '@typescript-eslint/no-unused-expressions': 'off',
  // Allow single-word component names for route views
  'vue/multi-word-component-names': 'off',
      // Vue formatting rules (prefer Prettier or manual style) -> relax to avoid noise
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/multiline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
      'vue/attributes-order': 'off',
      'vue/attribute-hyphenation': 'off',
      'vue/html-closing-bracket-spacing': 'off',
    },
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
    },
  },

  // Force vue-eslint-parser for Vue/TS/JS files, with TS as the inner script parser
  {
    files: ['**/*.{vue,js,ts,tsx}'],
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        parser: tsParser,
        ecmaVersion: 'latest',
        sourceType: 'module',
        extraFileExtensions: ['.vue'],
      },
    },
  },
]
