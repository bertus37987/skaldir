import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Die Werkbank liest die Komponenten READ-ONLY aus dem App-Spiegel (Hartlink-Baum,
// nie schreiben!). `thinking-orbs` liegt LOKAL in der Werkbank: ein absoluter Pfad in
// das Repo-node_modules zog dort eine ZWEITE React-Kopie heran → `resolveDispatcher() is null`.
const APP_SRC = '/home/willy/.hermes/godsv/app-src/apps/desktop/src'
const ORB_DOOR = '/home/willy/projects/gods-voice/app-overlay/gv/gv-orb.tsx'

export default defineConfig({
  plugins: [react()],
  resolve: {
    // Eine einzige React-Instanz für Werkbank, Orb und Host-Tür erzwingen.
    dedupe: ['react', 'react-dom'],
    alias: {
      '@': APP_SRC,
      // Die Host-Tür NICHT relativ importieren: der Cosmos-Renderer läuft mit eigenem
      // Root, ein ../../../../-Pfad liegt außerhalb seiner erlaubten Wurzeln (Vite: 500).
      'gv-orb': ORB_DOOR
    }
  },
  server: {
    fs: {
      allow: [
        '/home/willy/.hermes/godsv/app-src',
        '/home/willy/projects/gods-voice/tools/ui-workbench',
        '/home/willy/projects/gods-voice/app-overlay',
        '/home/willy/projects/gods-voice/node_modules'
      ]
    }
  }
})
