// 前端脚本（src/parse_video_py/static/js/，浏览器原生 ES 模块）的检查规则，和后端 ruff 的口径一致：
// 复杂度 ≤ 10、嵌套 ≤ 4 层、没用的变量和没定义的名字都算错。CI 里：npx eslint@10
const browser = Object.fromEntries(
  [
    'window', 'document', 'navigator', 'location', 'console', 'fetch', 'performance', 'innerHeight',
    'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'requestAnimationFrame', 'addEventListener',
    'matchMedia', 'IntersectionObserver', 'AbortController', 'File', 'FormData', 'URLSearchParams', 'Uint8Array',
  ].map((name) => [name, 'readonly']),
);

export default [
  {
    files: ['src/parse_video_py/static/js/**/*.js'],
    languageOptions: { ecmaVersion: 2022, sourceType: 'module', globals: browser },
    rules: {
      'no-undef': 'error',
      'no-unused-vars': ['error', { args: 'none', caughtErrors: 'none' }],
      'no-unreachable': 'error',
      'no-dupe-keys': 'error',
      'no-duplicate-imports': 'error',
      'no-shadow': 'error',
      'no-var': 'error',
      'prefer-const': 'error',
      eqeqeq: ['error', 'smart'],
      complexity: ['error', 10],
      'max-depth': ['error', 4],
      'max-nested-callbacks': ['error', 3],
      'max-params': ['error', 5],
      'max-lines-per-function': ['error', { max: 80, skipBlankLines: true, skipComments: true }],
    },
  },
];
