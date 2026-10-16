import type { CapacitorConfig } from '@capacitor/cli';

/**
 * Native iOS/Android shell around the DevAgent PWA.
 *
 * The web bundle (``dist/``) is exactly the same one served by the gateway at
 * ``/mobile``; inside the shell the app runs from ``capacitor://localhost``, so
 * the gateway URL and the shared token are configured in the app's Settings
 * screen (see ``src/components/GatewayConfigCard.tsx``).
 *
 * Build:
 *   npm run build && npx cap sync ios && npx cap open ios
 */
const config: CapacitorConfig = {
  appId: 'xyz.cshub.devagent',
  appName: 'DevAgent',
  webDir: 'dist',
  bundledWebRuntime: false,
  backgroundColor: '#090d16',
  ios: {
    contentInset: 'always',
    limitsNavigationsToAppBoundDomains: false,
  },
  server: {
    // Keep the https scheme so fetch/EventSource behave like in the browser.
    iosScheme: 'capacitor',
  },
  plugins: {
    SplashScreen: {
      backgroundColor: '#090d16',
      showSpinner: false,
    },
  },
};

export default config;
