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
      // Require strict equality everywhere except intentional `value == null`
      // null-or-undefined checks (the one allowed loose form).
      eqeqeq: ['error', 'always', { null: 'ignore' }],
      // A Promise executor that returns a value is almost always a bug (the
      // return is ignored) — flag it as an error.
      'no-promise-executor-return': 'error',
      // Constant binary expressions (e.g. always-truthy assertions) are dead
      // logic — flag them as an error.
      'no-constant-binary-expression': 'error',
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
