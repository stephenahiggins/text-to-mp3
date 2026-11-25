import React, { useEffect, useRef, useState } from 'react';
import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import { ActivityIndicator, NativeEventEmitter, NativeModules, Platform, Image, Alert, AppState, Linking, Modal, Pressable, ScrollView, StatusBar, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as DocumentPicker from 'expo-document-picker';
import { setAudioModeAsync, useAudioPlayer, useAudioPlayerStatus } from 'expo-audio';
import { importFile, library, receive, removeRecording, savePosition, playbackSpeed, savePlaybackSpeed, PLAYBACK_SPEEDS, type Recording } from './src/library';
import type { Document } from './src/manifest';

const ink = '#202b24', green = '#315c42';
function clock(seconds: number) { const n = Math.max(0, Math.floor(seconds || 0)); return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`; }
function Button({ title, onPress, disabled = false, quiet = false }: { title: string; onPress: () => void; disabled?: boolean; quiet?: boolean }) {
  return <Pressable accessibilityRole="button" disabled={disabled} onPress={onPress} style={({ pressed }) => [s.button, quiet && s.quiet, (disabled || pressed) && { opacity: .5 }]}><Text style={[s.buttonText, quiet && { color: ink }]}>{title}</Text></Pressable>;
}

function PlaybackButton({ label, icon, onPress, disabled = false, primary = false, loading = false }: { label: string; icon: React.ComponentProps<typeof MaterialCommunityIcons>['name']; onPress: () => void; disabled?: boolean; primary?: boolean; loading?: boolean }) {
  return <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ disabled, busy: loading }} disabled={disabled} onPress={onPress} style={({ pressed }) => [s.playbackButton, primary ? s.primaryPlaybackButton : s.quiet, (disabled || pressed) && { opacity: .5 }]}>
    {loading ? <ActivityIndicator color="#fff" /> : <MaterialCommunityIcons name={icon} size={primary ? 26 : 22} color={primary ? '#fff' : ink} accessible={false} />}
  </Pressable>;
}

function Player({ recording, onClose, onDocument, onError }: { recording: Recording; onClose: () => void; onDocument: (d: Document) => void; onError: (message: string) => void }) {
  const player = useAudioPlayer(recording.uri, { updateInterval: 1000 });
  const status = useAudioPlayerStatus(player);
  const [rate, setRate] = useState(playbackSpeed), [trackWidth, setTrackWidth] = useState(1);
  const restored = useRef(false), latest = useRef(recording.position), lastSaved = useRef(-1);
  const current = recording.manifest.chapters.find(c => status.currentTime >= c.start && status.currentTime < c.end);
  useEffect(() => {
    if (!status.isLoaded) return;
    try { player.setPlaybackRate(rate); }
    catch (e) { onError(`Unable to set playback speed: ${(e as Error).message}`); }
  }, [player, status.isLoaded, rate]);
  useEffect(() => {
    const subscription = player.addListener('playbackStatusUpdate', update => {
      const error = (update as typeof update & { error?: string }).error;
      if (error) onError(`Unable to play this recording: ${error}`);
    });
    return () => subscription.remove();
  }, [player]);
  useEffect(() => {
    if (!status.isLoaded || restored.current) return;
    restored.current = true;
    void (async () => {
      try {
        await setAudioModeAsync({ playsInSilentMode: true, shouldPlayInBackground: true, interruptionMode: 'doNotMix' });
        await player.seekTo(Math.min(recording.position, Math.max(0, status.duration - 1)));
        player.setActiveForLockScreen(true, { title: recording.manifest.title, artist: 'Reading Stacks' }, { showSeekBackward: true, showSeekForward: true });
      } catch (e) { onError((e as Error).message); }
    })();
  }, [status.isLoaded, player, recording]);
  useEffect(() => {
    if (!restored.current || !status.isLoaded) return;
    latest.current = status.currentTime;
    const bucket = Math.floor(status.currentTime / 5);
    if (bucket !== lastSaved.current || !status.playing) {
      savePosition(recording.id, status.currentTime); lastSaved.current = bucket;
    }
  }, [status.currentTime, status.playing, status.isLoaded]);
  useEffect(() => {
    const sub = AppState.addEventListener('change', state => { if (state !== 'active') savePosition(recording.id, latest.current); });
    return () => { sub.remove(); savePosition(recording.id, latest.current); };
  }, [recording.id]);
  function seek(seconds: number) { void player.seekTo(Math.max(0, Math.min(status.duration, seconds))).catch(e => onError(e.message)); }
  return <View style={s.player}>
    <View style={s.row}><Text style={s.eyebrow}>NOW LISTENING · OFFLINE</Text><Button title="Close" quiet onPress={onClose} /></View>
    <Text style={s.title}>{recording.manifest.title}</Text>
    <Text style={s.muted}>{current?.title || 'Stack audio'}</Text>
    <Pressable accessibilityRole="adjustable" accessibilityLabel="Playback position" accessibilityValue={{ min: 0, max: Math.floor(status.duration), now: Math.floor(status.currentTime) }} accessibilityActions={[{ name: 'increment' }, { name: 'decrement' }]} onAccessibilityAction={e => seek(status.currentTime + (e.nativeEvent.actionName === 'increment' ? 30 : -15))} onLayout={e => setTrackWidth(e.nativeEvent.layout.width)} onPress={e => { if (status.isLoaded) seek(e.nativeEvent.locationX / trackWidth * status.duration); }} style={{ paddingVertical: 12 }}>
      <View style={s.track}><View style={[s.fill, { width: `${status.duration ? Math.min(100, status.currentTime / status.duration * 100) : 0}%` }]} /></View>
    </Pressable>
    <View style={s.row}><Text style={s.muted}>{clock(status.currentTime)}</Text><Text style={s.muted}>{clock(status.duration)}</Text></View>
    <View style={s.playbackControls}>
      <PlaybackButton label="Skip back 15 seconds" icon="rewind-15" disabled={!status.isLoaded} onPress={() => seek(status.currentTime - 15)} />
      <PlaybackButton label={!status.isLoaded ? 'Loading audio' : status.playing ? 'Pause' : 'Play'} icon={status.playing ? 'pause' : 'play'} primary loading={!status.isLoaded} disabled={!status.isLoaded} onPress={() => {
        if (status.playing) player.pause(); else { if (status.didJustFinish || status.currentTime >= status.duration - .2) seek(0); player.play(); }
      }} />
      <PlaybackButton label="Skip forward 30 seconds" icon="fast-forward-30" disabled={!status.isLoaded} onPress={() => seek(status.currentTime + 30)} />
    </View>
    <Text style={s.eyebrow}>PLAYBACK SPEED</Text>
    <View style={s.controls}>
      {PLAYBACK_SPEEDS.map(speed => <Pressable key={speed} accessibilityRole="radio" accessibilityLabel={`${speed} times playback speed`} accessibilityState={{ checked: rate === speed, disabled: !status.isLoaded }} disabled={!status.isLoaded} onPress={() => {
        try { player.setPlaybackRate(speed); savePlaybackSpeed(speed); setRate(speed); }
        catch (e) { onError(`Unable to set playback speed: ${(e as Error).message}`); }
      }} style={({ pressed }) => [s.button, rate !== speed && s.quiet, (pressed || !status.isLoaded) && { opacity: .5 }]}>
        <Text style={[s.buttonText, rate !== speed && { color: ink }]}>{speed}×</Text>
      </Pressable>)}
    </View>
    <Text style={s.eyebrow}>ARTICLES</Text>
    {!recording.manifest.chapters.length && <Text style={s.muted}>This recording has no chapter markers. Regenerate older stacks on your Mac to add them.</Text>}
    {recording.manifest.chapters.map((chapter, index) => <View style={[s.chapter, current === chapter && s.currentChapter]} key={index}>
      <Pressable accessibilityRole="button" accessibilityLabel={`Play ${chapter.title}`} onPress={() => { seek(chapter.start); player.play(); }} style={{ flex: 1 }}>
        <Text style={s.chapterTitle}>{chapter.title}</Text><Text style={s.muted}>{clock(chapter.start)} · {clock(chapter.end - chapter.start)}</Text>
      </Pressable>
      {chapter.document > 0 && <Button title="Read" quiet onPress={() => onDocument(recording.manifest.documents[chapter.document - 1])} />}
    </View>)}
    {!recording.manifest.chapters.length && recording.manifest.documents.map((document, index) => <Button key={index} title={`Read ${document.title}`} quiet onPress={() => onDocument(document)} />)}
    <Text style={s.muted}>Version {recording.manifest.revision} · {new Date(recording.manifest.generatedAt * 1000).toLocaleDateString()}</Text>
  </View>;
}

function Main() {
  const [savedShareCount, setSavedShareCount] = useState(0);
  useEffect(() => {
    if (Platform.OS !== 'ios') return;
    const queue = NativeModules.ReadingShareQueue;
    if (!queue) return;
    const changes = new NativeEventEmitter(queue).addListener('savedSharesChanged', event => {
      const count = (event as { count?: unknown }).count;
      if (typeof count === 'number') setSavedShareCount(count);
    });
    queue.setActive(AppState.currentState === 'active');
    const sub = AppState.addEventListener('change', state => queue?.setActive(state === 'active'));
    return () => { sub.remove(); changes.remove(); queue.setActive(false); };
  }, []);
  const [tab, setTab] = useState<'current' | 'library'>('current');
  const [items, setItems] = useState<Recording[]>([]), [selected, setSelected] = useState<Recording | null>(null);
  const [url, setUrl] = useState(''), [busy, setBusy] = useState(false), [progress, setProgress] = useState(''), [error, setError] = useState('');
  const [scanning, setScanning] = useState(false), [document, setDocument] = useState<Document | null>(null);
  const [permission, requestPermission] = useCameraPermissions();
  const scroll = useRef<React.ComponentRef<typeof ScrollView>>(null);
  const working = useRef(false), scanned = useRef(false);
  useEffect(() => { setItems(library()); }, []);
  useEffect(() => { scroll.current?.scrollTo({ y: 0, animated: false }); }, [tab]);
  async function run(operation: () => Promise<Recording>) {
    if (working.current) return;
    working.current = true; setBusy(true); setError('');
    try { const recording = await operation(); setItems(library()); setSelected(recording); setTab('current'); setProgress('Saved for offline listening'); }
    catch (e) { setError((e as Error).message + ' For Wi-Fi transfers, keep your Mac awake and both devices on the same network.'); setProgress(''); }
    finally { working.current = false; setBusy(false); }
  }
  function download(link: string) { setUrl(link); void run(() => receive(link, setProgress)); }
  useEffect(() => {
    void Linking.getInitialURL().then(link => { if (link?.startsWith('articlestacks://receive?')) download(link); });
    const sub = Linking.addEventListener('url', ({ url: link }) => { if (link.startsWith('articlestacks://receive?')) download(link); });
    return () => sub.remove();
  }, []);
  return <SafeAreaView style={s.safe}>
    <StatusBar barStyle="dark-content" />
    <ScrollView ref={scroll} contentContainerStyle={s.page} keyboardShouldPersistTaps="handled">
      <View style={s.brand}><Image source={require('./assets/reading-stacks.png')} style={{width:52,height:52}} accessibilityLabel="Reading Stacks Logo"/><View><Text style={s.brandName}>Reading Stacks</Text><Text style={s.muted}>When there's just no time to read</Text></View></View>
      {Platform.OS === 'ios' && NativeModules.ReadingShareQueue && <Button
        title={savedShareCount > 0 ? `${savedShareCount} shared ${savedShareCount === 1 ? 'article' : 'articles'} · view stacks` : 'Mac connection'}
        quiet onPress={() => NativeModules.ReadingShareQueue.present()} />}
      {!!error && <Text accessibilityRole="alert" style={s.error}>{error}</Text>}
      <View style={[s.tabContent, tab !== 'current' && s.hidden]}>
        {selected ? <Player key={selected.id} recording={selected} onClose={() => { setSelected(null); setItems(library()); }} onDocument={setDocument} onError={setError} /> : <View style={s.card}>
          <Text style={s.title}>No current stack</Text>
          <Text style={s.muted}>Choose a recording from your library, or receive a new stack from your Mac.</Text>
          <Button title="Open Library" onPress={() => { setItems(library()); setTab('library'); }} />
        </View>}
      </View>
      <View style={[s.tabContent, tab !== 'library' && s.hidden]}>
      <View style={s.card}>
        <Text style={s.title}>Receive a stack</Text>
        <Text style={s.muted}>On your Mac, choose Send to Phone. Scan the code here or paste the transfer link.</Text>
        <View style={s.controls}>
          <Button title="Scan QR code" disabled={busy} onPress={() => { void (async () => {
            const p = permission?.granted ? permission : await requestPermission();
            if (p.granted) { scanned.current = false; setScanning(true); } else setError('Camera access is disabled. Paste the transfer link instead.');
          })().catch(e => setError(e.message)); }} />
          <Button title="Import file" quiet disabled={busy} onPress={() => { void run(async () => {
            const picked = await DocumentPicker.getDocumentAsync({ type: '*/*', copyToCacheDirectory: true });
            if (picked.canceled) throw Error('Import cancelled.');
            setProgress('Importing and verifying…');
            return importFile(picked.assets[0].uri, picked.assets[0].name);
          }); }} />
        </View>
        <TextInput accessibilityLabel="Transfer link" style={s.input} value={url} onChangeText={setUrl} autoCapitalize="none" autoCorrect={false} placeholder="Paste transfer link" placeholderTextColor="#7f887f" editable={!busy} />
        <Button title={busy ? 'Receiving…' : 'Download stack'} disabled={busy || !url.trim()} onPress={() => download(url)} />
        {!!progress && <View style={s.controls}>{busy && <ActivityIndicator color={green} />}<Text accessibilityLiveRegion="polite" style={s.muted}>{progress}</Text></View>}
      </View>
      <View style={s.row}><Text style={s.title}>Your library</Text><Text style={s.muted}>{items.length} recordings</Text></View>
      {!items.length && <View style={s.card}><Text style={s.title}>You don't have any stacks at the moment!</Text><Text style={s.muted}>Downloaded stacks stay on this device. Import .stack exports or MP3 files, or send a recording from your Mac.</Text></View>}
      {items.map(item => <View style={s.card} key={item.id}>
        <Text style={s.eyebrow}>SAVED OFFLINE · VERSION {item.manifest.revision}</Text>
        <Text style={s.title}>{item.manifest.title}</Text>
        <Text style={s.muted}>{item.manifest.documents.length} articles · {clock(item.position)} listened</Text>
        {items.some(other => other.manifest.stackId === item.manifest.stackId && other.manifest.generatedAt > item.manifest.generatedAt) && <Text style={s.muted}>A newer recording is in your library.</Text>}
        <View style={s.controls}><Button title={item.position > 0 ? 'Continue listening' : 'Open player'} onPress={() => { setSelected(library().find(r => r.id === item.id) || null); setTab('current'); }} />
          <Button title="Remove" quiet onPress={() => Alert.alert('Remove download?', 'This removes the recording from this device. Your Mac copy is kept.', [
            { text: 'Cancel', style: 'cancel' }, { text: 'Remove', style: 'destructive', onPress: () => {
              if (selected?.id === item.id) setSelected(null);
              void removeRecording(item).then(() => setItems(library())).catch(e => setError(e.message));
            } }
          ])} />
        </View>
      </View>)}
      </View>
    </ScrollView>
    <View accessibilityRole="tablist" style={s.tabs}>
      {([{ id: 'current', title: 'Current Stack', icon: 'playlist-play' }, { id: 'library', title: 'Library', icon: 'bookshelf' }] as const).map(item => <Pressable key={item.id} accessibilityRole="tab" accessibilityState={{ selected: tab === item.id }} onPress={() => { setItems(library()); setTab(item.id); }} style={({ pressed }) => [s.tab, tab === item.id && s.activeTab, pressed && { opacity: .5 }]}>
        <MaterialCommunityIcons name={item.icon} size={24} color={tab === item.id ? green : '#667065'} accessible={false} />
        <Text style={[s.tabLabel, tab === item.id && { color: green }]}>{item.title}</Text>
      </Pressable>)}
    </View>
    <Modal visible={scanning} onRequestClose={() => setScanning(false)} animationType="slide">
      <SafeAreaView style={s.safe}><View style={s.page}><Text style={s.title}>Scan the code on your Mac</Text><Button title="Cancel" quiet onPress={() => setScanning(false)} /></View>
        {scanning && <CameraView style={{ flex: 1 }} barcodeScannerSettings={{ barcodeTypes: ['qr'] }} onBarcodeScanned={({ data }) => {
          if (scanned.current) return; scanned.current = true; setScanning(false); download(data);
        }} />}
      </SafeAreaView>
    </Modal>
    <Modal visible={!!document} onRequestClose={() => setDocument(null)} animationType="slide">
      <SafeAreaView style={s.safe}><ScrollView contentContainerStyle={s.page}>
        <Button title="Back to player" quiet onPress={() => setDocument(null)} />
        <Text style={s.heading}>{document?.title}</Text><Text style={s.muted}>{[document?.author, document?.date].filter(Boolean).join(' · ')}</Text>
        {!!document?.url && /^https?:\/\//i.test(document.url) && <Button title="Open source" quiet onPress={() => { void Linking.openURL(document.url).catch(e => setError(e.message)); }} />}
         {!!document?.briefing_summary && <><Text style={s.prose}>Briefing</Text><Text selectable style={s.prose}>{document.briefing_summary}</Text></>}
        <Text selectable style={s.prose}>{document?.prepared}</Text>
      </ScrollView></SafeAreaView>
    </Modal>
  </SafeAreaView>;
}
export default function App() { return <SafeAreaProvider><Main /></SafeAreaProvider>; }

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#f5f3eb' }, page: { padding: 22, gap: 20, paddingBottom: 40 },
  tabContent: { gap: 20 }, hidden: { display: 'none' },
  tabs: { flexDirection: 'row', gap: 8, padding: 12, borderTopWidth: 1, borderTopColor: '#e0e2d6', backgroundColor: '#fffef9' },
  tab: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 4, paddingVertical: 10, borderRadius: 16, minHeight: 64 },
  activeTab: { backgroundColor: '#e9ebdf' }, tabLabel: { fontSize: 14, fontWeight: '700', color: '#667065' },
  brand: { flexDirection: 'row', alignItems: 'center', gap: 12 }, logo: { fontSize: 38, fontWeight: '900', color: green, letterSpacing: -5, marginRight: 8 }, brandName: { fontSize: 20, fontWeight: '700', color: ink },
  heading: { fontSize: 42, lineHeight: 46, fontWeight: '700', color: ink, letterSpacing: -1.5 }, title: { fontSize: 21, lineHeight: 28, fontWeight: '700', color: ink },
  muted: { color: '#667065', fontSize: 14, lineHeight: 21 }, eyebrow: { color: green, fontSize: 10, fontWeight: '800', letterSpacing: 1.1 },
  card: { backgroundColor: '#fffef9', padding: 20, borderRadius: 20, gap: 12, borderWidth: 1, borderColor: '#e0e2d6' },
  button: { backgroundColor: green, paddingHorizontal: 16, paddingVertical: 13, borderRadius: 24, alignItems: 'center', minHeight: 44 }, buttonText: { color: '#fff', fontWeight: '700', fontSize: 14 }, quiet: { backgroundColor: '#e9ebdf' },
  controls: { flexDirection: 'row', gap: 8, alignItems: 'center', flexWrap: 'wrap' }, row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 8 },
  playbackControls: { flexDirection: 'row', gap: 14, alignItems: 'center', justifyContent: 'center' },
  playbackButton: { width: 44, height: 44, borderRadius: 22, alignItems: 'center', justifyContent: 'center' },
  primaryPlaybackButton: { width: 48, height: 48, borderRadius: 24, backgroundColor: green },
  input: { borderWidth: 1, borderColor: '#cbd1c1', padding: 14, borderRadius: 12, fontSize: 15, color: ink }, error: { color: '#a03829', lineHeight: 21 },
  player: { backgroundColor: '#edf0e3', paddingHorizontal: 20, paddingVertical: 12, borderRadius: 20, gap: 8 }, track: { height: 5, backgroundColor: '#cfd7c4', borderRadius: 3, overflow: 'hidden' }, fill: { height: 5, backgroundColor: green },
  chapter: { flexDirection: 'row', alignItems: 'center', padding: 10, gap: 8, borderRadius: 12 }, currentChapter: { backgroundColor: '#dce4cf' }, chapterTitle: { fontSize: 16, lineHeight: 23, fontWeight: '600', color: ink },
  prose: { fontSize: 18, lineHeight: 29, color: ink }
});
