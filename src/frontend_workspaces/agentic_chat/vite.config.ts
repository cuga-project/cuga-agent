import { defineConfig, ViteUserConfig } from 'vitest/config';
import react from '@vitejs/plugin-react-swc';
import * as path from 'path';

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const fakeStream = process.env.FAKE_STREAM === 'true';
  
  let options: ViteUserConfig = {
    plugins: [react()],
    define: {
      FAKE_STREAM: JSON.stringify(fakeStream),
    },
    build: {
      sourcemap: true,
      commonjsOptions: {
        transformMixedEsModules: true,
        include: [/shared/, /node_modules/],
      },
    },
    test: {
      globals: true,
      testTimeout: 20000,
      // 'node' — jsdom is not installed in this workspace; vitest exits 1 when
      // the configured environment package is missing, even for pure-logic
      // tests. DOM-needing tests can opt in per-file via @vitest-environment.
      environment: 'node',
      include: [
        'src/components/**/*.test.{js,jsx,ts,tsx}',
        'src/snapshot-tests/*.test.tsx',
        'src/pages/**/*.test.{js,jsx,ts,tsx}',
        'src/hooks/**/*.test.{js,jsx,ts,tsx}',
        'src/citations/**/*.test.{js,jsx,ts,tsx}',
      ],
    },
    resolve: {
      alias: {
        '@uiagent/shared': path.resolve(__dirname, '../shared/src'),
        '@agentic_chat': path.resolve(__dirname, './src'),
      },
    },
  };

  if (mode === 'test') {
    options = {
      ...options,
      base: './',
      build: {
        ...options.build,
        outDir: 'src/snapshot-tests/templates',
        rollupOptions: {
          input: 'src/snapshot-tests/snapshot.html',
        },
      },
    };
  }

  return options;
});
