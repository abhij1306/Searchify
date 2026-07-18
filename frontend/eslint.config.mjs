import coreWebVitals from 'eslint-config-next/core-web-vitals';
import typescript from 'eslint-config-next/typescript';

// eslint-config-next 16 ships native flat configs, so the old
// `FlatCompat.extends('next/core-web-vitals', 'next/typescript')` bridge is
// replaced by direct imports.
const eslintConfig = [
  ...coreWebVitals,
  ...typescript,
  {
    rules: {
      // `_`-prefixed bindings mark intentional omissions (e.g. the
      // destructure-to-omit pattern in tests).
      '@typescript-eslint/no-unused-vars': [
        'warn',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
          ignoreRestSiblings: true,
        },
      ],
    },
  },
  {
    ignores: [
      'node_modules/**',
      '.next/**',
      'out/**',
      'coverage/**',
      'playwright-report/**',
      'test-results/**',
      'next-env.d.ts',
    ],
  },
];

export default eslintConfig;
