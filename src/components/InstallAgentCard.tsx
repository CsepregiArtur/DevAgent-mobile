import React, { useState } from 'react';
import { getGatewayBase } from '../services/api';
import {
  Apple,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Download,
  Monitor,
  Terminal,
} from 'lucide-react';

const REPO = 'CsepregiArtur/DevAgent-mobile';
const RELEASE = `https://github.com/${REPO}/releases/latest/download`;

type Platform = 'macos' | 'windows';

/**
 * "Install the agent" card: one copy-paste command that downloads the compiled
 * binary from GitHub Releases, then a first-run wizard that asks for the settings
 * (gateway URL, agent token, agent id, workspace folders) and verifies the
 * connection. Works the same on macOS and Windows; Python is not required.
 */
export const InstallAgentCard: React.FC = () => {
  const [platform, setPlatform] = useState<Platform>('macos');
  const [appleSilicon, setAppleSilicon] = useState(true);
  const [copied, setCopied] = useState(false);
  const [showSource, setShowSource] = useState(false);

  const gateway = getGatewayBase() || 'https://deepseek-api.cs-hub.xyz/mobile';
  const wsUrl = gateway.replace(/^http/, 'ws') + '/agent/ws';

  const macAsset = appleSilicon ? 'devagent-agent-macos-arm64' : 'devagent-agent-macos-x64';
  const macCommand = [
    `curl -L -o devagent-agent ${RELEASE}/${macAsset}`,
    'chmod +x devagent-agent',
    'xattr -dr com.apple.quarantine devagent-agent   # o singură dată (build nesemnat)',
    './devagent-agent --setup',
  ].join('\n');

  const windowsCommand = [
    `Invoke-WebRequest -Uri ${RELEASE}/devagent-agent-windows-x64.exe -OutFile devagent-agent.exe`,
    '.\\devagent-agent.exe --setup',
  ].join('\n');

  const sourceCommand = [
    'git clone https://github.com/' + REPO + '.git',
    'cd DevAgent-mobile',
    'python3 -m pip install --user websockets',
    'python3 agent/devagent_agent.py --setup',
  ].join('\n');

  const command = platform === 'macos' ? macCommand : windowsCommand;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked: the command stays selectable */
    }
  };

  return (
    <div className="p-4 rounded-2xl bg-zinc-900 border border-zinc-800 space-y-3 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <div className="p-2 rounded-lg bg-sky-500/10 text-sky-400 border border-sky-500/20">
            <Download className="w-4 h-4" />
          </div>
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-white">
              Install the agent
            </h3>
            <p className="text-[11px] text-zinc-400">
              Download the binary from GitHub Releases — no Python needed
            </p>
          </div>
        </div>
        <button
          onClick={copy}
          className="flex items-center gap-1 text-[11px] px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition shrink-0"
        >
          {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
          <span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>

      {/* Platform switch */}
      <div className="grid grid-cols-2 gap-1.5 p-1 rounded-xl bg-zinc-950 border border-zinc-800">
        <button
          type="button"
          onClick={() => setPlatform('macos')}
          className={`py-1.5 rounded-lg text-[11px] font-medium transition flex items-center justify-center gap-1.5 ${
            platform === 'macos' ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:text-zinc-200'
          }`}
        >
          <Apple className="w-3.5 h-3.5" /> macOS
        </button>
        <button
          type="button"
          onClick={() => setPlatform('windows')}
          className={`py-1.5 rounded-lg text-[11px] font-medium transition flex items-center justify-center gap-1.5 ${
            platform === 'windows' ? 'bg-zinc-800 text-white' : 'text-zinc-400 hover:text-zinc-200'
          }`}
        >
          <Monitor className="w-3.5 h-3.5" /> Windows
        </button>
      </div>

      {platform === 'macos' && (
        <div className="flex items-center gap-2 text-[10px] text-zinc-400">
          <span>Procesor:</span>
          <button
            type="button"
            onClick={() => setAppleSilicon(true)}
            className={`px-2 py-0.5 rounded-full border font-mono transition ${
              appleSilicon
                ? 'border-indigo-500/50 bg-indigo-500/10 text-indigo-300'
                : 'border-zinc-800 text-zinc-500'
            }`}
          >
            Apple Silicon
          </button>
          <button
            type="button"
            onClick={() => setAppleSilicon(false)}
            className={`px-2 py-0.5 rounded-full border font-mono transition ${
              !appleSilicon
                ? 'border-indigo-500/50 bg-indigo-500/10 text-indigo-300'
                : 'border-zinc-800 text-zinc-500'
            }`}
          >
            Intel
          </button>
        </div>
      )}

      <div className="p-2.5 rounded-xl bg-black border border-zinc-800 font-mono text-[10px] text-zinc-300 overflow-x-auto whitespace-pre">
        {command}
      </div>

      <div className="space-y-1 text-[11px] text-zinc-400 leading-relaxed">
        <p className="text-zinc-300 font-medium">La prima rulare, wizard-ul te întreabă:</p>
        <ul className="list-disc list-inside space-y-0.5 text-zinc-400">
          <li>
            URL-ul gateway-ului:{' '}
            <span className="font-mono text-zinc-300">{wsUrl}</span>
          </li>
          <li>
            tokenul de agent — generează unul mai sus, la <strong>API keys</strong> (kind{' '}
            <span className="font-mono">agent</span>)
          </li>
          <li>
            un <span className="font-mono">agent id</span> unic și folderele de lucru permise
          </li>
        </ul>
        <p className="text-zinc-500">
          Apoi verifică singur conexiunea (WebSocket outbound, fără porturi deschise). Necesită{' '}
          <span className="font-mono">git</span> pe mașină.
        </p>
      </div>

      <button
        type="button"
        onClick={() => setShowSource((value) => !value)}
        className="text-[10px] text-zinc-500 hover:text-zinc-300 flex items-center gap-1 transition"
      >
        {showSource ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        <Terminal className="w-3 h-3" />
        sau rulează din sursă (necesită Python 3.9+)
      </button>
      {showSource && (
        <div className="p-2.5 rounded-xl bg-black border border-zinc-800 font-mono text-[10px] text-zinc-300 overflow-x-auto whitespace-pre">
          {sourceCommand}
        </div>
      )}
    </div>
  );
};
