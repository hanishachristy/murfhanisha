'use client';

import { useMemo, useState, useEffect, useRef, type CSSProperties } from 'react';
import { RoomEvent, TokenSource } from 'livekit-client';
import {
  useSession,
  useVoiceAssistant,
  useTranscriptions,
  useLocalParticipant,
  useChat,
  useRoomContext,
} from '@livekit/components-react';
import { WarningIcon } from '@phosphor-icons/react/dist/ssr';
import type { AppConfig } from '@/app-config';
import { AgentSessionProvider } from '@/components/agents-ui/agent-session-provider';
import { StartAudioButton } from '@/components/agents-ui/start-audio-button';
import { Toaster } from '@/components/ui/sonner';
import { useAgentErrors } from '@/hooks/useAgentErrors';
import { useDebugMode } from '@/hooks/useDebug';
import { getSandboxTokenSource } from '@/lib/utils';

const IN_DEVELOPMENT = process.env.NODE_ENV !== 'production';

function AppSetup() {
  useDebugMode({ enabled: IN_DEVELOPMENT });
  useAgentErrors();
  return null;
}

interface AppProps {
  appConfig: AppConfig;
}

interface CartItem {
  id: string;
  name: string;
  quantity: number;
  unit: string;
  emoji: string;
}

interface CartEventPayload {
  action: 'cart';
  operation: 'add' | 'remove' | 'clear' | 'sync';
  item?: CartItem;
  itemId?: string;
  items?: CartItem[];
  message?: string;
}

interface AnalyticsState {
  totalCalls: number;
  successfulCalls: number;
  failedCalls: number;
}

export function App({ appConfig }: AppProps) {
  const tokenSource = useMemo(() => {
    return typeof process.env.NEXT_PUBLIC_CONN_DETAILS_ENDPOINT === 'string'
      ? getSandboxTokenSource(appConfig)
      : TokenSource.endpoint('/api/token');
  }, [appConfig]);

  const session = useSession(
    tokenSource,
    appConfig.agentName ? { agentName: appConfig.agentName } : undefined
  );

  return (
    <AgentSessionProvider session={session}>
      <AppSetup />
      <GroceryAssistantUI session={session} />

      <StartAudioButton label="Start Audio" />

      <Toaster
        icons={{
          warning: <WarningIcon weight="bold" />,
        }}
        position="top-center"
        className="toaster group"
        style={
          {
            '--normal-bg': '#142024',
            '--normal-text': '#F4F6F7',
            '--normal-border': '#1E3036',
          } as CSSProperties
        }
      />
    </AgentSessionProvider>
  );
}


// ============================================================
// AMBIENT STORE SOUND
// ============================================================

function useAmbientSound() {
  const [isPlaying, setIsPlaying] = useState(false);

  const audioCtxRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<AudioBufferSourceNode | null>(null);

  const toggleAmbient = async () => {
    try {
      const AudioContextClass =
        window.AudioContext ||
        (window as any).webkitAudioContext;

      if (!audioCtxRef.current) {
        audioCtxRef.current = new AudioContextClass();
      }

      const ctx = audioCtxRef.current;

      if (ctx.state === 'suspended') {
        await ctx.resume();
      }

      if (isPlaying || sourceRef.current) {
        if (sourceRef.current) {
          try {
            sourceRef.current.stop();
            sourceRef.current.disconnect();
          } catch (e) {}

          sourceRef.current = null;
        }

        setIsPlaying(false);
      } else {
        const bufferSize = ctx.sampleRate * 2;

        const buffer = ctx.createBuffer(
          1,
          bufferSize,
          ctx.sampleRate
        );

        const output = buffer.getChannelData(0);

        let b0 = 0;
        let b1 = 0;
        let b2 = 0;
        let b3 = 0;
        let b4 = 0;
        let b5 = 0;
        let b6 = 0;

        for (let i = 0; i < bufferSize; i++) {
          const white = Math.random() * 2 - 1;

          b0 = 0.99886 * b0 + white * 0.0555179;
          b1 = 0.99332 * b1 + white * 0.0750759;
          b2 = 0.96900 * b2 + white * 0.1538520;
          b3 = 0.86650 * b3 + white * 0.3104856;
          b4 = 0.55000 * b4 + white * 0.5329522;
          b5 = -0.7616 * b5 - white * 0.0168980;

          output[i] =
            b0 +
            b1 +
            b2 +
            b3 +
            b4 +
            b5 +
            b6 +
            white * 0.5362;

          output[i] *= 0.035;

          b6 = white * 0.115926;
        }

        const noise = ctx.createBufferSource();

        noise.buffer = buffer;
        noise.loop = true;

        const filter = ctx.createBiquadFilter();

        filter.type = 'lowpass';
        filter.frequency.value = 350;

        const gain = ctx.createGain();

        gain.gain.value = 0.25;

        noise.connect(filter);
        filter.connect(gain);
        gain.connect(ctx.destination);

        noise.start(0);

        sourceRef.current = noise;

        setIsPlaying(true);
      }
    } catch (e) {
      console.error('AudioContext error:', e);

      setIsPlaying(false);
      sourceRef.current = null;
    }
  };

  return {
    isPlaying,
    toggleAmbient,
  };
}


// ============================================================
// MAIN GROCERY ASSISTANT UI
// ============================================================

function GroceryAssistantUI({ session }: { session: any }) {
  const { state } = useVoiceAssistant();

  const transcripts = useTranscriptions();

  const { localParticipant } = useLocalParticipant();
  const room = useRoomContext();

  const {
    send: sendChatMessage,
    chatMessages,
  } = useChat();

  const [hasStartedOnce, setHasStartedOnce] = useState(false);

  const [micError, setMicError] = useState<string | null>(null);

  const [isMuted, setIsMuted] = useState(false);

  // ==========================================================
  // CHAT + NOTEPAD STATE
  // ==========================================================

  const [inputText, setInputText] = useState('');

  const [savedItems, setSavedItems] = useState<CartItem[]>([]);

  // ==========================================================
  // DAY 8 - CALL ANALYTICS
  // These values come from the backend SQLite call_analytics table.
  // ==========================================================

  const [analytics, setAnalytics] = useState<AnalyticsState>({
    totalCalls: 0,
    successfulCalls: 0,
    failedCalls: 0,
  });

  const [analyticsOpen, setAnalyticsOpen] = useState(true);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);

  // ==========================================================
  // AMBIENT SOUND
  // ==========================================================

  const {
    isPlaying: isAmbientPlaying,
    toggleAmbient,
  } = useAmbientSound();

  // ==========================================================
  // QUICK PROMPTS
  // ==========================================================

  const allPrompts = useMemo(
    () => [
      {
        label: 'Tomatoes available?',
        dotColor: 'bg-[var(--accent-gold)]',
      },
      {
        label: '1L Milk & Butter',
        dotColor: 'bg-[var(--text-main)]',
      },
      {
        label: '5kg Rice price?',
        dotColor: 'bg-[var(--accent-mint)]',
      },
      {
        label: 'What are shop timings?',
        dotColor: 'bg-[var(--accent-gold)]',
      },
      {
        label: "What's good for a rainy day snack?",
        dotColor: 'bg-[var(--accent-mint)]',
      },
      {
        label: 'Surprise me with a fresh fruit combo!',
        dotColor: 'bg-[var(--accent-gold)]',
      },
      {
        label: 'Are organic greens in stock?',
        dotColor: 'bg-[var(--accent-mint)]',
      },
      {
        label: 'Best spices for evening chai?',
        dotColor: 'bg-[var(--text-main)]',
      },
    ],
    []
  );

  const [activePrompts, setActivePrompts] = useState(
    allPrompts.slice(0, 4)
  );

  const shufflePrompts = () => {
    const shuffled = [...allPrompts].sort(
      () => 0.5 - Math.random()
    );

    setActivePrompts(shuffled.slice(0, 4));
  };

  const chatEndRef = useRef<HTMLDivElement>(null);

  const connState = session.connectionState;

  const messageTimestampsRef =
    useRef<Map<string, number>>(new Map());

  const globalCounterRef = useRef<number>(1);

  const activeCallIdRef = useRef<string | null>(null);

  // ==========================================================
  // LIVE TRANSCRIPT
  // ==========================================================
  // LiveKit's useTranscriptions() receives the lk.transcription
  // text stream. Each stream is updated as chunks arrive, so the
  // same message grows live while the user/agent is speaking.
  //
  // streamInfo.id is stable for a stream, which lets us replace
  // the interim text instead of creating duplicate bubbles.
  // ==========================================================

  const messages = useMemo(() => {
    const localIdentity = localParticipant?.identity;

    const voiceMessages = transcripts
      .filter((t: any) => Boolean(t?.text?.trim()))
      .map((t: any) => {
        const timestamp =
          Number(t?.streamInfo?.timestamp ?? 0) || Date.now();

        return {
          id: `voice-${t?.streamInfo?.id ?? `${t?.participantInfo?.identity}-${timestamp}`}`,
          text: t.text.trim(),
          sender:
            t?.participantInfo?.identity === localIdentity
              ? ('user' as const)
              : ('agent' as const),
          timestamp,
          isVoice: true,
          isFinal:
            t?.streamInfo?.attributes?.['lk.transcription_final'] ===
            'true',
        };
      });

    const textMessages = chatMessages
      .filter((msg: any) => Boolean(msg?.message?.trim()))
      .map((msg: any, index: number) => ({
        id: `chat-${msg?.timestamp ?? index}-${msg?.from?.identity ?? 'unknown'}`,
        text: msg.message.trim(),
        sender:
          msg?.from?.identity === localIdentity
            ? ('user' as const)
            : ('agent' as const),
        timestamp: Number(msg?.timestamp ?? Date.now()) || Date.now(),
        isVoice: false,
        isFinal: true,
      }));

    const combined = [...voiceMessages, ...textMessages];

    combined.sort((a, b) => a.timestamp - b.timestamp);

    // Keep the same stream/message only once.
    const seen = new Set<string>();

    return combined.filter((message) => {
      if (seen.has(message.id)) return false;
      seen.add(message.id);
      return true;
    });
  }, [transcripts, chatMessages, localParticipant?.identity]);

  // Keep the newest transcript visible while either side is speaking.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      chatEndRef.current?.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
      });
    }, 0);

    return () => window.clearTimeout(timer);
  }, [messages]);

  // ==========================================================
  // BACKEND -> FRONTEND CART SYNC
  //
  // The backend is the source of truth for the basket.
  // The frontend NEVER adds an item just because a user mentions it.
  // The agent must call the backend cart tool first. The backend then
  // publishes a structured "ui-events" packet and this listener updates
  // the basket.
  // ==========================================================

  useEffect(() => {
    const handleDataReceived = (
      payload: Uint8Array,
      _participant?: any,
      _kind?: any,
      topic?: string
    ) => {
      if (topic !== 'ui-events') return;

      try {
        const decoded = new TextDecoder().decode(payload);
        const event = JSON.parse(decoded) as
          | CartEventPayload
          | {
              action: 'toast';
              message?: string;
              type?: string;
            };

        // ======================================================
        // DAY 8 - LIVE CALL ANALYTICS
        // ======================================================

        if (
          event.event === 'call_started' ||
          event.event === 'call_finished' ||
          event.event === 'success_reached' ||
          event.action === 'call_analytics'
        ) {
          setAnalyticsLoading(false);

          if (event.callId) {
            activeCallIdRef.current = String(event.callId);
          }

          setAnalytics((current) => ({
            totalCalls:
              event.totalCalls !== undefined
                ? Number(event.totalCalls)
                : current.totalCalls,
            successfulCalls:
              event.successfulCalls !== undefined
                ? Number(event.successfulCalls)
                : current.successfulCalls,
            failedCalls:
              event.failedCalls !== undefined
                ? Number(event.failedCalls)
                : current.failedCalls,
          }));

          return;
        }

        if (event.action === 'cart') {
          const cartEvent = event as CartEventPayload;

          if (cartEvent.operation === 'sync') {
            setSavedItems(cartEvent.items ?? []);
            return;
          }

          if (cartEvent.operation === 'clear') {
            setSavedItems([]);
            return;
          }

          if (
            cartEvent.operation === 'remove' &&
            cartEvent.itemId
          ) {
            setSavedItems((current) =>
              current.filter(
                (item) => item.id !== cartEvent.itemId
              )
            );
            return;
          }

          if (
            cartEvent.operation === 'add' &&
            cartEvent.item
          ) {
            setSavedItems((current) => {
              const incoming = cartEvent.item!;

              const withoutExisting = current.filter(
                (item) => item.id !== incoming.id
              );

              return [...withoutExisting, incoming];
            });
          }

          return;
        }

        if (event.action === 'toast' && event.message) {
          const toastType = event.type ?? 'default';

          if (toastType === 'success') {
            console.info(event.message);
          } else if (toastType === 'error') {
            console.error(event.message);
          } else {
            console.info(event.message);
          }
        }
      } catch (error) {
        console.error(
          'Failed to process backend ui-event:',
          error
        );
      }
    };

    room.on(RoomEvent.DataReceived, handleDataReceived);

    return () => {
      room.off(RoomEvent.DataReceived, handleDataReceived);
    };
  }, [room]);

  // ==========================================================
  // END BACKEND -> FRONTEND CART SYNC
  // ==========================================================

  // ==========================================================
  // START CALL
  // ==========================================================

  const handleStart = async () => {
    setMicError(null);
    setAnalyticsLoading(true);

    // Clear current basket when starting a completely new call.
    setSavedItems([]);

    try {
      await navigator.mediaDevices.getUserMedia({
        audio: true,
      });

      setHasStartedOnce(true);

      session.start();

    } catch (err: any) {
      if (
        err.name === 'NotAllowedError' ||
        err.name === 'PermissionDeniedError'
      ) {
        setMicError(
          'Microphone permission denied! Click the lock icon in your browser URL bar to allow mic access.'
        );
      } else {
        setMicError(
          'Unable to access microphone: ' +
          err.message
        );
      }
    }
  };


  // ==========================================================
  // END CALL
  // ==========================================================

  const handleEnd = async () => {
    try {
      if (connState === 'connected') {
        const payload = new TextEncoder().encode(
          JSON.stringify({
            action: 'end_call',
            callId: activeCallIdRef.current,
          })
        );

        await room.localParticipant.publishData(payload, {
          reliable: true,
          topic: 'call-control',
        });

        // Let the backend persist the final result and publish
        // the updated analytics before the room disconnects.
        await new Promise((resolve) =>
          window.setTimeout(resolve, 400)
        );
      }
    } catch (error) {
      console.error('Failed to send end_call:', error);
    } finally {
      session.end();
    }
  };


  // ==========================================================
  // MUTE
  // ==========================================================

  const toggleMute = async () => {
    if (!localParticipant) return;

    const nextMute = !isMuted;

    try {
      await localParticipant.setMicrophoneEnabled(
        !nextMute
      );

      localParticipant.audioTrackPublications.forEach(
        (publication) => {
          if (publication.track) {
            if (nextMute) {
              publication.track.mute();
            } else {
              publication.track.unmute();
            }
          }
        }
      );

    } catch (e) {
      console.error(
        'Mute toggle error:',
        e
      );
    }

    setIsMuted(nextMute);
  };


  // ==========================================================
  // QUICK PROMPT
  // ==========================================================

  const handleQuickPrompt = (
    item: { label: string }
  ) => {
    if (connState === 'connected') {
      sendChatMessage(item.label);
    }
  };


  // ==========================================================
  // SEND CHAT MESSAGE
  // ==========================================================

  const handleSendMessage = () => {
    if (!inputText.trim()) return;

    if (connState === 'connected') {
      sendChatMessage(inputText);
    }

    setInputText('');
  };


  // ==========================================================
  // MANUAL REMOVE
  // ==========================================================

  const removeItemFromNotepad = (
    item: CartItem
  ) => {
    if (connState !== 'connected') return;

    // Ask the agent/backend to remove it. The backend will emit the
    // authoritative cart event and the UI will update from that event.
    sendChatMessage(
      `Remove ${item.name} from my cart.`
    );
  };



  // ==========================================================
  // STATUS BADGE
  // ==========================================================

  let badge = {
    label: 'Ready',
    color:
      'bg-[var(--bg-card)] text-[var(--accent-mint)] border-[var(--card-border)]',
    subtext:
      'Click below to start ordering.',
  };


  if (connState === 'connecting') {
    badge = {
      label: 'Connecting...',
      color:
        'bg-[var(--bg-card)] text-[var(--accent-gold)] border-[var(--card-border)]',
      subtext:
        'Joining Zen Fresh AI assistant...',
    };

  } else if (
    connState === 'connected'
  ) {

    if (state === 'speaking') {
      badge = {
        label: 'Speaking',
        color:
          'bg-[var(--bg-card)] text-[var(--accent-gold)] border-[var(--accent-gold)]/40 animate-breathe',
        subtext:
          'Agent is replying...',
      };

    } else if (
      state === 'listening' ||
      state === 'thinking'
    ) {
      badge = {
        label:
          state === 'thinking'
            ? 'Thinking...'
            : 'Listening',

        color:
          'bg-[var(--bg-card)] text-[var(--accent-gold)] border-[var(--accent-gold)]/40 animate-breathe',

        subtext:
          state === 'thinking'
            ? 'Checking stock and prices...'
            : 'Listening to your request...',
      };

    } else {
      badge = {
        label: 'Listening',
        color:
          'bg-[var(--bg-card)] text-[var(--accent-gold)] border-[var(--card-border)]',
        subtext:
          'Speak or ask a question.',
      };
    }

  } else if (
    hasStartedOnce &&
    connState === 'disconnected'
  ) {
    badge = {
      label: 'Call ended',
      color:
        'bg-[var(--bg-card)] text-[var(--text-muted)] border-[var(--card-border)]',
      subtext:
        'Call completed. Click below to start again.',
    };
  }


  const isAgentSpeaking =
    state === 'speaking';

  const isAgentListeningOrThinking =
    state === 'listening' ||
    state === 'thinking';


  // ==========================================================
  // UI
  // ==========================================================

  return (
    <main
      className={`min-h-screen bg-[var(--bg-main)] text-[var(--text-main)] flex flex-col items-center justify-between p-4 md:p-8 relative overflow-hidden transition-all duration-700 ${
        isAmbientPlaying
          ? 'shadow-[inset_0_0_150px_rgba(224,159,62,0.18)]'
          : ''
      }`}
    >

      {/* Background */}

      <div
        className="absolute inset-0 pointer-events-none z-0 bg-cover bg-center opacity-[0.16] mix-blend-luminosity"
        style={{
          backgroundImage:
            `url('https://images.unsplash.com/photo-1542838132-92c53300491e?auto=format&fit=crop&w=1920&q=80')`,
        }}
      />

      <div className="absolute inset-0 pointer-events-none z-0 bg-gradient-to-b from-[var(--bg-main)]/90 via-[var(--bg-main)]/95 to-[var(--bg-main)]" />


      {/* Ambient aura */}

      {isAmbientPlaying && (
        <div className="absolute inset-0 pointer-events-none bg-[radial-gradient(circle_at_50%_30%,rgba(51,180,158,0.12),transparent_75%)] z-0 transition-opacity duration-700" />
      )}


      {/* =====================================================
          GLOBAL STYLES
      ===================================================== */}

      <style jsx global>{`

        :root {
          --bg-main: #0B1315;
          --bg-card: #142024;
          --card-border: #1E3036;
          --accent-gold: #E09F3E;
          --accent-mint: #33B49E;
          --text-main: #F4F6F7;
          --text-muted: #8A9EAB;
        }

        @keyframes breathe {
          0%, 100% {
            transform: scale(1);
            opacity: 0.85;
            box-shadow: 0 0 0px rgba(224, 159, 62, 0);
          }

          50% {
            transform: scale(1.03);
            opacity: 1;
            box-shadow: 0 0 32px rgba(224, 159, 62, 0.45);
          }
        }

        @keyframes pulsePing {
          0% {
            transform: scale(0.95);
            opacity: 0.8;
            box-shadow: 0 0 0 0 rgba(224, 159, 62, 0.5);
          }

          70% {
            transform: scale(1.15);
            opacity: 0;
            box-shadow: 0 0 0 16px rgba(224, 159, 62, 0);
          }

          100% {
            transform: scale(1.15);
            opacity: 0;
            box-shadow: 0 0 0 0 rgba(224, 159, 62, 0);
          }
        }

        @keyframes pulseGlowRing {
          0% {
            box-shadow: 0 0 0 0 rgba(224, 159, 62, 0.5);
          }

          70% {
            box-shadow: 0 0 0 10px rgba(224, 159, 62, 0);
          }

          100% {
            box-shadow: 0 0 0 0 rgba(224, 159, 62, 0);
          }
        }

        @keyframes cardEntrance {
          0% {
            opacity: 0;
            transform: translateY(15px);
          }

          100% {
            opacity: 1;
            transform: translateY(0);
          }
        }

        @keyframes bubbleExpand {
          0% {
            opacity: 0;
            transform: scale(0.92) translateY(6px);
          }

          100% {
            opacity: 1;
            transform: scale(1) translateY(0);
          }
        }

        @keyframes slideDownSpring {
          0% {
            opacity: 0;
            transform: translateY(-16px) scale(0.95);
          }

          60% {
            opacity: 1;
            transform: translateY(4px) scale(1.01);
          }

          100% {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }

        @keyframes waveformPulse {
          0%, 100% {
            transform: scaleY(0.35);
          }

          50% {
            transform: scaleY(1.25);
          }
        }

        .animate-waveform-1 {
          animation: waveformPulse 0.75s ease-in-out infinite;
        }

        .animate-waveform-2 {
          animation: waveformPulse 0.55s ease-in-out infinite 0.15s;
        }

        .animate-waveform-3 {
          animation: waveformPulse 0.65s ease-in-out infinite 0.3s;
        }

        .animate-breathe {
          animation: breathe 3.5s ease-in-out infinite;
        }

        .animate-pulse-ping {
          animation: pulsePing 2.5s cubic-bezier(0.4, 0, 0.6, 1) infinite;
        }

        .animate-cta-pulse {
          animation: pulseGlowRing 2s infinite;
        }

        .animate-card-1 {
          animation: cardEntrance 0.5s cubic-bezier(0.16, 1, 0.3, 1) 0.1s forwards;
          opacity: 0;
        }

        .animate-card-2 {
          animation: cardEntrance 0.5s cubic-bezier(0.16, 1, 0.3, 1) 0.2s forwards;
          opacity: 0;
        }

        .animate-card-3 {
          animation: cardEntrance 0.5s cubic-bezier(0.16, 1, 0.3, 1) 0.3s forwards;
          opacity: 0;
        }

        .animate-bubble-expand {
          animation: bubbleExpand 0.35s cubic-bezier(0.16, 1, 0.3, 1) forwards;
        }

        .animate-notepad-spring {
          animation: slideDownSpring 0.45s cubic-bezier(0.175, 0.885, 0.32, 1.275) forwards;
        }

      `}</style>


      {/* =====================================================
          HEADER
      ===================================================== */}

      <header className="w-full max-w-6xl flex items-center justify-between mt-2 mb-6 relative z-10">

        <div>

          <div className="inline-flex items-center gap-2 px-3.5 py-1 rounded-full bg-[var(--bg-card)] border border-[var(--card-border)] text-[var(--accent-gold)] text-xs font-semibold mb-2 shadow-sm">
            🛒 Zen Fresh AI Voice & Chat Assistant
          </div>

          <div className="flex items-center gap-3">

            <div className="w-11 h-11 rounded-full bg-[var(--bg-card)] border border-[var(--accent-gold)]/60 flex items-center justify-center shadow-[0_0_20px_rgba(224,159,62,0.4)] shrink-0 p-1">

              <svg
                viewBox="0 0 48 48"
                className="w-full h-full drop-shadow-[0_0_8px_rgba(224,159,62,0.5)]"
              >

                <defs>

                  <linearGradient
                    id="goldGradient"
                    x1="0%"
                    y1="0%"
                    x2="100%"
                    y2="100%"
                  >
                    <stop
                      offset="0%"
                      stopColor="#F5D061"
                    />

                    <stop
                      offset="50%"
                      stopColor="#E09F3E"
                    />

                    <stop
                      offset="100%"
                      stopColor="#9C6B1E"
                    />
                  </linearGradient>

                  <linearGradient
                    id="mintGradient"
                    x1="0%"
                    y1="0%"
                    x2="0%"
                    y2="100%"
                  >
                    <stop
                      offset="0%"
                      stopColor="#45DFBF"
                    />

                    <stop
                      offset="100%"
                      stopColor="#33B49E"
                    />
                  </linearGradient>

                </defs>

                <circle
                  cx="24"
                  cy="24"
                  r="20"
                  fill="none"
                  stroke="url(#goldGradient)"
                  strokeWidth="3"
                  strokeDasharray="115 15"
                  strokeLinecap="round"
                  transform="rotate(-45 24 24)"
                />

                <g transform="translate(14, 12)">

                  <path
                    d="M 10 2 C 10 2 2 9 2 15 C 2 19.5 5.5 22 10 22 C 14.5 22 18 19.5 18 15 C 18 9 10 2 10 2 Z"
                    fill="url(#mintGradient)"
                  />

                  <path
                    d="M 10 6 C 9 12 7 16 3 19"
                    stroke="#0B1315"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    opacity="0.3"
                    fill="none"
                  />

                  <path
                    d="M 10 10 C 12 12 14 14 16 16"
                    stroke="#0B1315"
                    strokeWidth="1"
                    strokeLinecap="round"
                    opacity="0.2"
                    fill="none"
                  />

                  <path
                    d="M 10 14 C 11 15 12 16 13 17"
                    stroke="#0B1315"
                    strokeWidth="1"
                    strokeLinecap="round"
                    opacity="0.2"
                    fill="none"
                  />

                </g>

              </svg>

            </div>

            <div>

              <h1 className="text-3xl md:text-4xl font-bold tracking-tight text-[var(--text-main)]">
                ZEN FRESH AI
              </h1>

              <p className="text-[var(--text-muted)] text-sm mt-0.5">
                Check item availability via voice/chat, note grocery lists, and get instant store help.
              </p>

            </div>

          </div>

        </div>


        {/* Store Ambiance */}

        <button
          onClick={toggleAmbient}
          className={`flex items-center gap-2.5 px-4 py-2.5 rounded-xl border text-xs font-medium transition-all duration-300 shadow-lg cursor-pointer relative ${
            isAmbientPlaying
              ? 'bg-[var(--accent-gold)] text-[var(--bg-main)] border-[var(--accent-gold)] shadow-[0_0_20px_rgba(224,159,62,0.4)] ring-2 ring-[var(--accent-gold)]/40'
              : 'bg-[var(--bg-card)] text-[var(--text-main)] border-[var(--card-border)] hover:border-[var(--accent-gold)]/60'
          }`}
          title="Toggle Store Ambiance Audio"
        >

          <span
            className={`w-2 h-2 rounded-full ${
              isAmbientPlaying
                ? 'bg-[var(--bg-main)] animate-ping'
                : 'bg-[var(--accent-gold)]'
            }`}
          />

          <span>
            {isAmbientPlaying
              ? 'Ambiance Active'
              : 'Store Ambiance'}
          </span>

        </button>

      </header>


      {/* =====================================================
          DAY 8 — CALL ANALYTICS TOGGLE
      ===================================================== */}

      <div className="w-full max-w-6xl relative z-20 mb-5">
        <button
          type="button"
          onClick={() => setAnalyticsOpen((open) => !open)}
          className="w-full flex items-center justify-between px-4 py-3 rounded-2xl bg-[var(--bg-card)] border border-[var(--card-border)] hover:border-[var(--accent-gold)]/60 transition-all shadow-lg cursor-pointer"
        >
          <div className="flex items-center gap-3">
            <span className="w-2.5 h-2.5 rounded-full bg-[var(--accent-mint)] animate-pulse" />
            <div className="text-left">
              <p className="text-xs font-bold uppercase tracking-wider text-[var(--text-main)]">
                Call Analytics
              </p>
              <p className="text-[10px] text-[var(--text-muted)] mt-0.5">
                Live performance — updates from real calls
              </p>
            </div>
          </div>
          <span className="text-[var(--accent-gold)] text-xs font-bold">
            {analyticsOpen ? '▲ Hide' : '▼ Show'}
          </span>
        </button>

        {analyticsOpen && (
          <div className="grid grid-cols-3 gap-2 mt-2 animate-card-2">
            <div className="bg-[var(--bg-card)] border border-[var(--card-border)] rounded-xl p-3 text-center">
              <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">Total Calls</p>
              <p className="text-xl font-bold text-[var(--text-main)] mt-1">
                {analytics.totalCalls}
              </p>
              <p className="text-[9px] text-[var(--text-muted)] mt-0.5">Including active</p>
            </div>

            <div className="bg-[var(--bg-card)] border border-[var(--accent-mint)]/30 rounded-xl p-3 text-center">
              <p className="text-[10px] uppercase tracking-wider text-[var(--accent-mint)]">Successful</p>
              <p className="text-xl font-bold text-[var(--accent-mint)] mt-1">
                {analytics.successfulCalls}
              </p>
              <p className="text-[9px] text-[var(--text-muted)] mt-0.5">Completed successfully</p>
            </div>

            <div className="bg-[var(--bg-card)] border border-red-500/30 rounded-xl p-3 text-center">
              <p className="text-[10px] uppercase tracking-wider text-red-300">Failed</p>
              <p className="text-xl font-bold text-red-300 mt-1">
                {analytics.failedCalls}
              </p>
              <p className="text-[9px] text-[var(--text-muted)] mt-0.5">Did not reach success</p>
            </div>
          </div>
        )}

        {analyticsLoading && (
          <p className="text-[9px] text-[var(--accent-gold)] mt-2 text-center">
            Updating live call analytics...
          </p>
        )}
      </div>

      {/* =====================================================
          MAIN GRID
      ===================================================== */}

      <div className="w-full max-w-6xl grid grid-cols-1 md:grid-cols-12 gap-6 my-auto relative z-10">


        {/* ===================================================
            LEFT COLUMN
        =================================================== */}

        <div
          className={`md:col-span-4 bg-[var(--bg-card)] border rounded-2xl p-5 shadow-2xl flex flex-col items-center justify-between text-center transition-all duration-300 backdrop-blur-md animate-card-1 hover:border-[var(--accent-gold)] hover:shadow-[0_10px_35px_rgba(224,159,62,0.2)] relative overflow-hidden ${
            isAgentSpeaking ||
            isAgentListeningOrThinking ||
            connState === 'connected'
              ? 'border-[var(--accent-gold)] shadow-[0_0_30px_rgba(224,159,62,0.25)] ring-2 ring-[var(--accent-gold)]/30 animate-breathe'
              : 'border-[var(--card-border)]'
          }`}
        >

          <div
            className="absolute inset-0 pointer-events-none z-0 bg-cover bg-center opacity-[0.08] mix-blend-overlay"
            style={{
              backgroundImage:
                `url('https://images.unsplash.com/photo-1588964895597-cfccd6e2dbf9?auto=format&fit=crop&w=800&q=80')`,
            }}
          />


          <div className="w-full flex flex-col items-center relative z-10">

            {/* Status */}

            <div className="relative mb-2 flex items-center justify-center">

              {(isAgentSpeaking ||
                isAgentListeningOrThinking ||
                connState === 'connected') && (
                <div className="absolute inset-0 rounded-full border border-[var(--accent-gold)]/70 pointer-events-none animate-pulse-ping" />
              )}

              <span
                className={`px-4 py-1.5 rounded-full text-xs font-semibold border ${badge.color} transition-all shadow-sm relative z-10`}
              >
                {badge.label}
              </span>

            </div>


            {/* Audio Visualizer */}

            <div className="h-32 flex flex-col items-center justify-center my-2 w-full rounded-2xl transition-all duration-300 bg-[var(--bg-main)]/90 border border-[var(--card-border)] relative overflow-hidden px-4">

              <div className="flex items-center justify-center gap-1.5 h-14 px-6 rounded-xl bg-[var(--bg-card)] border border-[var(--accent-gold)]/40 shadow-[0_0_25px_rgba(224,159,62,0.22)] relative z-10">

                <span
                  className={`w-1.5 rounded-full transition-all duration-300 ${
                    state === 'speaking'
                      ? 'bg-[var(--accent-gold)] animate-waveform-1 h-10 shadow-[0_0_10px_rgba(224,159,62,0.8)]'
                      : state === 'listening' ||
                        state === 'thinking'
                      ? 'bg-[var(--accent-gold)] animate-waveform-2 h-8 shadow-[0_0_8px_rgba(224,159,62,0.6)]'
                      : 'bg-[var(--accent-gold)]/60 h-3'
                  }`}
                />

                <span
                  className={`w-1.5 rounded-full transition-all duration-300 ${
                    state === 'speaking'
                      ? 'bg-[var(--accent-gold)] animate-waveform-2 h-12 shadow-[0_0_12px_rgba(224,159,62,0.9)]'
                      : state === 'listening' ||
                        state === 'thinking'
                      ? 'bg-[var(--accent-gold)] animate-waveform-3 h-10 shadow-[0_0_10px_rgba(224,159,62,0.7)]'
                      : 'bg-[var(--accent-gold)]/60 h-4'
                  }`}
                />

                <span
                  className={`w-1.5 rounded-full transition-all duration-300 ${
                    state === 'speaking'
                      ? 'bg-[var(--accent-gold)] animate-waveform-3 h-14 shadow-[0_0_15px_rgba(224,159,62,1)]'
                      : state === 'listening' ||
                        state === 'thinking'
                      ? 'bg-[var(--accent-gold)] animate-waveform-1 h-11 shadow-[0_0_10px_rgba(224,159,62,0.8)]'
                      : 'bg-[var(--accent-gold)]/60 h-5'
                  }`}
                />

                <span
                  className={`w-1.5 rounded-full transition-all duration-300 ${
                    state === 'speaking'
                      ? 'bg-[var(--accent-gold)] animate-waveform-2 h-12 shadow-[0_0_12px_rgba(224,159,62,0.9)]'
                      : state === 'listening' ||
                        state === 'thinking'
                      ? 'bg-[var(--accent-gold)] animate-waveform-3 h-10 shadow-[0_0_10px_rgba(224,159,62,0.7)]'
                      : 'bg-[var(--accent-gold)]/60 h-4'
                  }`}
                />

                <span
                  className={`w-1.5 rounded-full transition-all duration-300 ${
                    state === 'speaking'
                      ? 'bg-[var(--accent-gold)] animate-waveform-1 h-9 shadow-[0_0_10px_rgba(224,159,62,0.8)]'
                      : state === 'listening' ||
                        state === 'thinking'
                      ? 'bg-[var(--accent-gold)] animate-waveform-2 h-8 shadow-[0_0_8px_rgba(224,159,62,0.6)]'
                      : 'bg-[var(--accent-gold)]/60 h-3'
                  }`}
                />

              </div>


              <div className="flex items-center gap-1.5 mt-2.5 relative z-10">

                <span className="w-2 h-2 rounded-full bg-[var(--accent-gold)] animate-ping shadow-[0_0_8px_rgba(224,159,62,1)]" />

                <span className="text-[10px] uppercase tracking-widest font-bold text-[var(--accent-gold)] drop-shadow-[0_0_6px_rgba(224,159,62,0.4)]">
                  {state === 'speaking'
                    ? 'Speaking'
                    : state === 'listening'
                    ? 'Listening'
                    : state === 'thinking'
                    ? 'Thinking'
                    : 'Ready'}
                </span>

              </div>

            </div>


            <p className="text-[var(--text-muted)] text-xs mt-2 mb-4">
              {badge.subtext}
            </p>


            {micError && (
              <div className="w-full bg-red-500/15 border border-red-500/30 text-red-200 text-xs p-3 rounded-lg mb-4 text-left">
                ⚠️ {micError}
              </div>
            )}

          </div>


          {/* =================================================
              QUICK PROMPTS
          ================================================= */}

          <div className="w-full my-3 text-left relative z-10">

            <div className="flex items-center justify-between mb-2">

              <span className="text-xs font-semibold text-[var(--accent-gold)] uppercase tracking-wider">
                Daily Specials Roulette
              </span>

              <button
                onClick={shufflePrompts}
                className="text-xs text-[var(--accent-gold)] hover:text-[var(--text-main)] flex items-center gap-1 transition-colors cursor-pointer bg-[var(--bg-main)] px-2 py-0.5 rounded-md border border-[var(--accent-gold)]/40 shadow-[0_0_10px_rgba(224,159,62,0.15)]"
                title="Shuffle Suggestions"
              >
                🎲 Shuffle
              </button>

            </div>


            <div className="flex flex-wrap gap-1.5">

              {activePrompts.map(
                (item, i) => (
                  <button
                    key={i}
                    onClick={() =>
                      handleQuickPrompt({
                        label: item.label,
                      })
                    }
                    className="text-xs bg-[var(--bg-main)]/90 text-[var(--text-main)] border border-[var(--card-border)] px-3 py-2 rounded-xl transition-all duration-200 transform hover:-translate-y-0.5 hover:shadow-[0_0_15px_rgba(224,159,62,0.25)] hover:bg-[var(--bg-card)] hover:text-[var(--accent-gold)] hover:border-[var(--accent-gold)] flex items-center gap-2.5 w-full cursor-pointer"
                  >

                    <span
                      className={`w-2 h-2 rounded-full ${item.dotColor} shadow-[0_0_6px_rgba(224,159,62,0.5)] shrink-0`}
                    />

                    <span className="truncate">
                      {item.label}
                    </span>

                  </button>
                )
              )}

            </div>

          </div>


          {/* =================================================
              CONTROLS
          ================================================= */}

          <div className="w-full flex gap-2 mt-2 relative z-10">

            {connState === 'connected' && (
              <button
                onClick={toggleMute}
                className={`px-3 py-2.5 rounded-xl border text-xs font-medium transition-colors ${
                  isMuted
                    ? 'bg-amber-500/20 text-amber-300 border-amber-500/30'
                    : 'bg-[var(--bg-main)] hover:bg-[var(--card-border)] text-[var(--text-main)] border-[var(--card-border)]'
                }`}
              >
                {isMuted ? '🔇' : '🎙️'}
              </button>
            )}


            {connState === 'disconnected' ? (

              <button
                onClick={handleStart}
                className="flex-1 bg-[var(--accent-gold)] hover:bg-[#C98B32] text-[var(--bg-main)] font-bold py-2.5 rounded-xl text-xs transition-all shadow-lg shadow-[var(--accent-gold)]/30 animate-cta-pulse cursor-pointer"
              >
                {hasStartedOnce
                  ? 'Start Again'
                  : 'Start Voice Call'}
              </button>

            ) : (

              <button
                onClick={handleEnd}
                disabled={
                  connState === 'connecting'
                }
                className="flex-1 bg-red-600/80 hover:bg-red-700 disabled:opacity-50 text-[var(--text-main)] font-medium py-2.5 rounded-xl text-xs transition-colors shadow-lg shadow-red-600/20 cursor-pointer"
              >
                {connState === 'connecting'
                  ? 'Connecting...'
                  : 'End Call'}
              </button>

            )}

          </div>

        </div>


        {/* ===================================================
            MIDDLE COLUMN
        =================================================== */}

        <div className="md:col-span-5 bg-[var(--bg-card)] border border-[var(--card-border)] rounded-2xl p-4 flex flex-col h-[440px] shadow-xl backdrop-blur-md animate-card-2 hover:border-[var(--accent-gold)]/60 hover:shadow-[0_10px_30px_rgba(224,159,62,0.08)] transition-all duration-300">

          <div className="flex items-center justify-between pb-3 border-b border-[var(--card-border)] mb-3 shrink-0">

            <span className="text-xs font-semibold text-[var(--text-main)] uppercase tracking-wider flex items-center gap-1.5">

              <span className="w-2 h-2 rounded-full bg-[var(--accent-gold)] animate-pulse shadow-[0_0_8px_rgba(224,159,62,0.8)]"></span>

              Live Chat & Transcripts

            </span>

            <span className="text-xs text-[var(--accent-gold)] font-medium">
              Voice & Text
            </span>

          </div>


          {/* Chat */}

          <div className="flex-1 min-h-0 overflow-y-auto space-y-3 pr-2 scrollbar-thin scrollbar-thumb-[var(--card-border)]">

            {messages.length === 0 ? (

              <div className="h-full flex flex-col items-center justify-center text-[var(--text-muted)] text-xs text-center px-4">

                <p>
                  Speak into your microphone or type your query below to chat with Zen Fresh AI.
                </p>

              </div>

            ) : (

              messages.map((msg) => {

                const isAgent =
                  msg.sender === 'agent';

                return (
                  <div
                    key={msg.id}
                    className={`flex flex-col animate-bubble-expand ${
                      isAgent
                        ? 'items-start'
                        : 'items-end'
                    }`}
                  >

                    <span className="text-[10px] text-[var(--accent-gold)] mb-0.5 font-medium flex items-center gap-1">

                      {isAgent && (
                        <span className="text-[10px]">
                          🌿
                        </span>
                      )}

                      {isAgent
                        ? 'Zen Fresh AI'
                        : 'You'}

                    </span>


                    <div
                      className={`max-w-[85%] rounded-xl px-3.5 py-2 text-xs leading-relaxed ${
                        isAgent
                          ? 'bg-[var(--bg-main)] text-[var(--text-main)] border border-[var(--accent-gold)]/30 shadow-[0_0_10px_rgba(224,159,62,0.08)]'
                          : 'bg-[var(--bg-card)] text-[var(--text-main)] border border-[var(--card-border)]'
                      }`}
                    >
                      {msg.text}
                      {msg.isVoice && !msg.isFinal && (
                        <span className="ml-1 inline-block animate-pulse text-[var(--accent-gold)]">
                          …
                        </span>
                      )}
                    </div>

                  </div>
                );
              })

            )}

            <div ref={chatEndRef} />

          </div>


          {/* Text Input */}

          <div className="pt-3 border-t border-[var(--card-border)] flex gap-2 mt-2 shrink-0">

            <input
              type="text"
              placeholder="Type your message here..."
              value={inputText}
              onChange={(e) =>
                setInputText(e.target.value)
              }
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  handleSendMessage();
                }
              }}
              className="flex-1 bg-[var(--bg-main)] border border-[var(--card-border)] rounded-xl px-3 py-2 text-xs text-[var(--text-main)] placeholder-[var(--text-muted)]/60 focus:outline-none focus:border-[var(--accent-gold)] focus:shadow-[0_0_10px_rgba(224,159,62,0.2)]"
            />

            <button
              onClick={handleSendMessage}
              className="bg-[var(--accent-gold)] hover:bg-[#C98B32] text-[var(--bg-main)] border border-[var(--card-border)] text-xs px-4 py-2 rounded-xl transition-colors font-bold shadow-md shadow-[var(--accent-gold)]/20 cursor-pointer"
            >
              Send
            </button>

          </div>

        </div>


        {/* ===================================================
            RIGHT COLUMN — NOTEPAD
        =================================================== */}

        <div className="md:col-span-3 bg-[var(--bg-card)] border border-[var(--card-border)] rounded-2xl p-4 flex flex-col justify-between h-[440px] shadow-xl backdrop-blur-md animate-card-3 hover:border-[var(--accent-gold)]/60 hover:shadow-[0_10px_30px_rgba(224,159,62,0.08)] transition-all duration-300">

          <div>

            <div className="flex items-center justify-between pb-3 border-b border-[var(--card-border)] mb-3">

              <span className="text-xs font-semibold text-[var(--text-main)] uppercase tracking-wider flex items-center gap-1.5">

                <span className="text-[var(--accent-gold)] drop-shadow-[0_0_8px_rgba(224,159,62,0.6)]">
                  🛒
                </span>

                Produce Basket

              </span>

              <span className="text-xs text-[var(--accent-gold)] font-bold">
                {savedItems.length} items
              </span>

            </div>


            <p className="text-[11px] text-[var(--accent-gold)]/90 mb-3 font-medium">
              Confirmed items with actual available quantity:
            </p>


            <div className="flex flex-col gap-2 max-h-[250px] overflow-y-auto pr-1">

              {savedItems.length === 0 ? (

                <div className="text-xs text-[var(--text-muted)]/70 italic text-center py-10">
                  Your cart is empty. Confirm items with the assistant to fill your basket!
                </div>

              ) : (

                savedItems.map(
                  (item, idx) => (

                    <div
                      key={idx}
                      className="flex items-center justify-between bg-[var(--bg-main)] border border-[var(--accent-gold)]/30 text-[var(--text-main)] text-xs px-3 py-2 rounded-xl animate-notepad-spring shadow-[0_0_12px_rgba(224,159,62,0.1)]"
                    >

                      <div className="flex items-center gap-2 truncate mr-2">

                        <span className="text-base">
                          {item.emoji}
                        </span>
                        <span className="truncate font-medium text-[var(--text-main)]">
                          {item.quantity} {item.unit} {item.name}
                        </span>

                      </div>


                      <button
                        onClick={() =>
                          removeItemFromNotepad(
                            idx
                          )
                        }
                        className="text-[var(--text-muted)] hover:text-[var(--accent-gold)] font-bold px-1 transition-colors cursor-pointer"
                        title="Remove item"
                      >
                        ×
                      </button>

                    </div>

                  )
                )

              )}

            </div>

          </div>


          <div className="pt-3 border-t border-[var(--card-border)] flex flex-col gap-2 text-center">

            <span className="text-[10px] text-[var(--text-muted)] block">
              Synced with{' '}
              <span className="text-[var(--accent-gold)] font-medium">
                Zen Fresh AI
              </span>{' '}
              Assistant
            </span>

          </div>

        </div>

      </div>


      {/* =====================================================
          FOOTER
      ===================================================== */}

      <footer className="text-xs text-[var(--text-muted)] mt-6 mb-2 relative z-10">
        Powered by{' '}
        <span className="text-[var(--text-main)] font-medium">
          Zen Fresh AI
        </span>{' '}
        & LiveKit
      </footer>

    </main>
  );
}