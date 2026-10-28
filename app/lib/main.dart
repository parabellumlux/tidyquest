import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:share_plus/share_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';

void main() => runApp(const TidyQuestApp());

class TidyQuestApp extends StatelessWidget {
  const TidyQuestApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'TidyQuest',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.teal),
        useMaterial3: true,
      ),
      home: const TidyQuestPage(),
    );
  }
}

enum Stage { home, scanning, inventory, energy, quests, rescan, differ, card }

const energyLabels = {
  'barely-standing': 'I can barely stand',
  '10-minutes': '10 minutes',
  'an-hour': 'An hour',
  'full-send': 'Full send',
};

class TidyQuestPage extends StatefulWidget {
  const TidyQuestPage({super.key});

  @override
  State<TidyQuestPage> createState() => _TidyQuestPageState();
}

class _TidyQuestPageState extends State<TidyQuestPage> {
  final _picker = ImagePicker();
  final _proxyCtrl = TextEditingController(text: 'http://100.102.190.123:8001');
  // BYOK: the user's own Meta Model API key. Ships EMPTY by default --
  // the app runs the local pipeline without it. When live Meta AI
  // features turn on, the key is sent per request from this field only.
  final _keyCtrl = TextEditingController();

  TidyQuestApi get _api => TidyQuestApi(
        _proxyCtrl.text.trim(),
        apiKey: _keyCtrl.text.trim(),
      );

  // Space type is explicit ("room" default); a vehicle interior must be
  // declared -- it is never guessed from a photo filename.
  final String _spaceType = 'room';

  Stage _stage = Stage.home;
  String _status = '';
  String? _error;

  // Session state.
  Uint8List? _beforeBytes;
  String _beforeName = 'photo.jpg';
  Map<String, dynamic>? _beforeInventory;
  Uint8List? _overlayBytes;
  Map<String, dynamic>? _privacyReport;
  String _labelBackend = '';
  Map<String, dynamic>? _plan;
  Set<String> _done = {};
  Set<String> _skipped = {};
  Uint8List? _afterBytes;
  String _afterName = 'after.jpg';
  Map<String, dynamic>? _afterInventory;
  Map<String, dynamic>? _differ;
  Uint8List? _cardBytes;
  Map<String, dynamic>? _sidecar;
  bool _angleMismatch = false;
  bool _demoMode = false;
  // Generation counter: bumped by _reset and by leaving the scanning
  // screen, so a late completion from an abandoned operation is ignored
  // instead of yanking the user to another screen.
  int _opGen = 0;

  // Session persistence (AUDIT-1 fix 2): the session is saved to
  // app-private on-device storage on every stage change and restored
  // on launch, so a dead phone can no longer eat a quest. The proxy
  // URL and the BYOK key are NEVER persisted. Restored quests land
  // on the current task: the quest screen names the first open step
  // (see _firstOpenStep). There is no Focus Mode UI in this build
  // yet, so restore targets the existing quest UI, not a Focus Mode
  // task.
  static const _sessionKey = 'tidyquest_session_v1';
  bool _restored = false;

  void _persist() {
    unawaited(_saveSession());
  }

  Map<String, dynamic> _sessionDoc() {
    String? b64(Uint8List? bytes) =>
        bytes == null ? null : base64Encode(bytes);
    return {
      'version': 1,
      'stage': _stage.name,
      'beforeName': _beforeName,
      'beforeB64': b64(_beforeBytes),
      'beforeInventory': _beforeInventory,
      'overlayB64': b64(_overlayBytes),
      'privacyReport': _privacyReport,
      'labelBackend': _labelBackend,
      'plan': _plan,
      'done': _done.toList(),
      'skipped': _skipped.toList(),
      'afterName': _afterName,
      'afterB64': b64(_afterBytes),
      'afterInventory': _afterInventory,
      'differ': _differ,
      'cardB64': b64(_cardBytes),
      'sidecar': _sidecar,
      'angleMismatch': _angleMismatch,
      'demoMode': _demoMode,
    };
  }

  Future<void> _saveSession() async {
    // Never persist the transient scanning stage: a kill mid-scan
    // restores to the last completed stage, not a stuck spinner.
    if (_stage == Stage.scanning) return;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_sessionKey, jsonEncode(_sessionDoc()));
    } catch (_) {
      // Persistence must never crash the quest itself; the worst
      // case is a lost session, which is the pre-fix behavior.
    }
  }

  Future<void> _clearSession() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_sessionKey);
    } catch (_) {}
  }

  Map<String, dynamic>? _firstOpenStep() {
    final plan = _plan;
    if (plan == null) return null;
    for (final q in plan['quests'] as List) {
      for (final s in q['steps'] as List) {
        final step = s as Map<String, dynamic>;
        final id = step['id'] as String;
        if (!_done.contains(id) && !_skipped.contains(id)) return step;
      }
    }
    return null;
  }

  Future<void> _restoreSession() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(_sessionKey);
      if (raw == null) return;
      final data = jsonDecode(raw) as Map<String, dynamic>;
      if (!mounted) return;
      Uint8List? dec(dynamic v) =>
          v == null ? null : base64Decode(v as String);
      var stage = Stage.values.firstWhere(
        (s) => s.name == data['stage'],
        orElse: () => Stage.home,
      );
      final plan = data['plan'] == null
          ? null
          : Map<String, dynamic>.from(data['plan'] as Map);
      final differ = data['differ'] == null
          ? null
          : Map<String, dynamic>.from(data['differ'] as Map);
      final cardBytes = dec(data['cardB64']);
      if (stage == Stage.scanning) stage = Stage.home;
      if (stage == Stage.quests && plan == null) stage = Stage.home;
      if (stage == Stage.differ && differ == null) stage = Stage.home;
      if (stage == Stage.card && cardBytes == null) stage = Stage.home;
      setState(() {
        _stage = stage;
        _beforeName = data['beforeName'] as String? ?? 'photo.jpg';
        _beforeBytes = dec(data['beforeB64']);
        _beforeInventory = data['beforeInventory'] == null
            ? null
            : Map<String, dynamic>.from(data['beforeInventory'] as Map);
        _overlayBytes = dec(data['overlayB64']);
        _privacyReport = data['privacyReport'] == null
            ? null
            : Map<String, dynamic>.from(data['privacyReport'] as Map);
        _labelBackend = data['labelBackend'] as String? ?? '';
        _plan = plan;
        _done = Set<String>.from(data['done'] as List? ?? []);
        _skipped = Set<String>.from(data['skipped'] as List? ?? []);
        _afterName = data['afterName'] as String? ?? 'after.jpg';
        _afterBytes = dec(data['afterB64']);
        _afterInventory = data['afterInventory'] == null
            ? null
            : Map<String, dynamic>.from(data['afterInventory'] as Map);
        _differ = differ;
        _cardBytes = cardBytes;
        _sidecar = data['sidecar'] == null
            ? null
            : Map<String, dynamic>.from(data['sidecar'] as Map);
        _angleMismatch = data['angleMismatch'] as bool? ?? false;
        _demoMode = data['demoMode'] as bool? ?? false;
        _restored = _stage != Stage.home && _plan != null;
      });
    } catch (_) {
      // A corrupt or incompatible saved session starts fresh.
    }
  }

  @override
  void initState() {
    super.initState();
    _restoreSession();
  }

  @override
  void dispose() {
    _proxyCtrl.dispose();
    super.dispose();
  }

  void _fail(Object e, [int? gen]) {
    // Ignore failures from an operation the user already abandoned.
    if (gen != null && gen != _opGen) return;
    setState(() {
      _error = e.toString();
      if (_stage == Stage.scanning) _stage = Stage.home;
    });
  }

  void _reset() {
    // Invalidate any in-flight operation so a late completion cannot
    // yank the user out of the fresh session.
    _opGen++;
    setState(() {
      _stage = Stage.home;
      _error = null;
      _status = '';
      _beforeBytes = null;
      _beforeInventory = null;
      _overlayBytes = null;
      _privacyReport = null;
      _plan = null;
      _done = {};
      _skipped = {};
      _afterBytes = null;
      _afterInventory = null;
      _differ = null;
      _cardBytes = null;
      _sidecar = null;
      _angleMismatch = false;
      _demoMode = false;
      _restored = false;
    });
    _clearSession();
  }

  Future<void> _testConnection() async {
    final gen = _opGen;
    try {
      await _api.checkHealth();
      if (mounted && gen == _opGen) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Connected to the TidyQuest proxy.')),
        );
      }
    } catch (e) {
      _fail(e, gen);
    }
  }

  Future<void> _pickAndScan(ImageSource source, {bool after = false}) async {
    final gen = _opGen;
    try {
      final picked = await _picker.pickImage(source: source);
      if (picked == null) return;
      final bytes = await picked.readAsBytes();
      setState(() {
        _error = null;
        _stage = Stage.scanning;
        _status = after
            ? 'Scanning your after photo…'
            : 'Scanning your room… this takes a couple of minutes on the first run.';
      });
      final result = await _api.inventory(bytes, picked.name);
      if (!mounted || gen != _opGen) return;
      setState(() {
        if (after) {
          _afterBytes = bytes;
          _afterName = picked.name;
          _afterInventory =
              result['inventory'] as Map<String, dynamic>;
        } else {
          _beforeBytes = bytes;
          _beforeName = picked.name;
          _beforeInventory =
              result['inventory'] as Map<String, dynamic>;
          _overlayBytes =
              base64Decode(result['overlay_b64'] as String);
          _privacyReport = result['privacy'] as Map<String, dynamic>?;
          _labelBackend = result['label_backend'] as String? ?? '';
          _demoMode = false;
        }
      });
      if (after) {
        await _runDiffer();
      } else {
        setState(() => _stage = Stage.inventory);
        await _saveSession();
      }
    } catch (e) {
      _fail(e, gen);
    }
  }

  Future<void> _buildPlan(String energy) async {
    final gen = _opGen;
    try {
      setState(() {
        _error = null;
        _stage = Stage.scanning;
        _status = 'The Questmaster is building your quest…';
      });
      final result = await _api.plan(_beforeInventory!, energy,
          spaceType: _spaceType);
      if (!mounted || gen != _opGen) return;
      setState(() {
        _plan = result['plan'] as Map<String, dynamic>;
        _done = {};
        _skipped = {};
        _restored = false;
        _stage = Stage.quests;
      });
      await _saveSession();
    } catch (e) {
      _fail(e, gen);
    }
  }

  Future<void> _reroute() async {
    final gen = _opGen;
    try {
      setState(() {
        _error = null;
        _stage = Stage.scanning;
        _status = 'Re-planning around the skipped steps… no scolding.';
      });
      final result = await _api.reroute(_plan!, _skipped.toList());
      if (!mounted || gen != _opGen) return;
      setState(() {
        _plan = result['plan'] as Map<String, dynamic>;
        final keptIds = <String>{};
        for (final q in _plan!['quests'] as List) {
          for (final s in q['steps'] as List) {
            keptIds.add(s['id'] as String);
          }
        }
        _done = _done.intersection(keptIds);
        _skipped = {};
        _restored = false;
        _stage = Stage.quests;
      });
      await _saveSession();
    } catch (e) {
      _fail(e, gen);
    }
  }

  Future<void> _runDiffer() async {
    final gen = _opGen;
    try {
      setState(() {
        _error = null;
        _stage = Stage.scanning;
        _status = 'Proving your progress…';
      });
      final result = await _api.differ(
        _beforeInventory!,
        _afterInventory!,
        angleMismatch: _angleMismatch,
        spaceType: _spaceType,
      );
      if (!mounted || gen != _opGen) return;
      setState(() {
        _differ = result['differ'] as Map<String, dynamic>;
        _stage = Stage.differ;
      });
      await _saveSession();
    } catch (e) {
      _fail(e, gen);
    }
  }

  Future<void> _makeCard() async {
    final gen = _opGen;
    try {
      setState(() {
        _error = null;
        _stage = Stage.scanning;
        _status = 'Composing your share card…';
      });
      final result = await _api.card(
        beforeBytes: _beforeBytes!,
        beforeName: _beforeName,
        afterBytes: _afterBytes!,
        afterName: _afterName,
        plan: _plan!,
        differ: _differ,
        beforeImageId: _beforeInventory?['image_id'] as String?,
        spaceType: _spaceType,
      );
      if (!mounted || gen != _opGen) return;
      setState(() {
        _cardBytes = base64Decode(result['card_b64'] as String);
        _sidecar = result['sidecar'] as Map<String, dynamic>?;
        _stage = Stage.card;
      });
      await _saveSession();
    } catch (e) {
      _fail(e, gen);
    }
  }

  Future<void> _loadDemo(String which) async {
    final gen = _opGen;
    try {
      setState(() {
        _error = null;
        _stage = Stage.scanning;
        _status = 'Loading the demo quest…';
      });
      final demo = await _api.demo(which);
      if (!mounted || gen != _opGen) return;
      setState(() {
        _plan = demo['plan'] as Map<String, dynamic>;
        _differ = demo['differ'] as Map<String, dynamic>;
        _cardBytes = base64Decode(demo['card_b64'] as String);
        _sidecar = demo['sidecar'] as Map<String, dynamic>?;
        _demoMode = true;
        _done = {};
        _skipped = {};
        _restored = false;
        _stage = Stage.quests;
      });
      await _saveSession();
    } catch (e) {
      _fail(e, gen);
    }
  }

  Future<void> _shareCard() async {
    if (_cardBytes == null) return;
    final gen = _opGen;
    try {
      final dir = Directory.systemTemp;
      final file = File('${dir.path}/tidyquest-card.png');
      await file.writeAsBytes(_cardBytes!);
      await Share.shareXFiles(
        [XFile(file.path)],
        text: _sidecar?['stats_text'] as String? ?? 'My TidyQuest win!',
      );
    } catch (e) {
      _fail(e, gen);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('TidyQuest'),
        actions: [
          if (_stage != Stage.home)
            IconButton(
              icon: const Icon(Icons.restart_alt),
              tooltip: 'Start a new room',
              onPressed: _reset,
            ),
        ],
      ),
      body: switch (_stage) {
        Stage.home => _homeScreen(),
        Stage.scanning => _scanningScreen(),
        Stage.inventory => _inventoryScreen(),
        Stage.energy => _energyScreen(),
        Stage.quests => _questScreen(),
        Stage.rescan => _rescanScreen(),
        Stage.differ => _differScreen(),
        Stage.card => _cardScreen(),
      },
    );
  }

  Widget _errorBanner() {
    if (_error == null) return const SizedBox.shrink();
    return Container(
      width: double.infinity,
      color: Colors.red.shade100,
      padding: const EdgeInsets.all(12),
      child: Text(_error!, style: const TextStyle(color: Colors.red)),
    );
  }

  Widget _privacyNote() {
    return const Padding(
      padding: EdgeInsets.all(12),
      child: Text(
        'Every photo shown or shared here is blurred for faces and mirrors '
        'before it leaves the pipeline.',
        style: TextStyle(fontStyle: FontStyle.italic),
        textAlign: TextAlign.center,
      ),
    );
  }

  Widget _homeScreen() {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const Text(
          'A photo of an overwhelming room becomes a winnable game.',
          style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 8),
        const Text(
          'Frozen, not messy. Your quest shrinks to fit the energy you '
          'actually have — never the reverse.',
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 24),
        TextField(
          controller: _proxyCtrl,
          decoration: const InputDecoration(
            labelText: 'Backend proxy URL',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 8),
        OutlinedButton(
          onPressed: _testConnection,
          child: const Text('Test connection'),
        ),
        const SizedBox(height: 16),
        TextField(
          controller: _keyCtrl,
          obscureText: true,
          decoration: const InputDecoration(
            labelText: 'Meta Model API key (bring your own)',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 4),
        const Text(
          'Optional for now — the app runs the local pipeline without '
          'it. When live Meta AI features turn on, paste your own Meta '
          'Model API key here (create one in your Meta Model API '
          'account). The key stays on this phone and is sent only with '
          'your own requests; it is never baked into the app.',
          style: TextStyle(fontSize: 13),
        ),
        const SizedBox(height: 24),
        FilledButton.icon(
          onPressed: () => _pickAndScan(ImageSource.camera),
          icon: const Icon(Icons.camera_alt),
          label: const Text('Snap the messy room'),
        ),
        const SizedBox(height: 8),
        FilledButton.icon(
          onPressed: () => _pickAndScan(ImageSource.gallery),
          icon: const Icon(Icons.photo_library),
          label: const Text('Pick a photo'),
        ),
        const SizedBox(height: 24),
        const Divider(),
        const SizedBox(height: 8),
        const Text('Or walk a demo quest, no photo needed:'),
        const SizedBox(height: 8),
        OutlinedButton(
          onPressed: () => _loadDemo('car'),
          child: const Text('Demo: car backseat'),
        ),
        const SizedBox(height: 8),
        OutlinedButton(
          onPressed: () => _loadDemo('donna'),
          child: const Text("Demo: Donna's bathroom"),
        ),
        const SizedBox(height: 16),
        _errorBanner(),
      ],
    );
  }

  Widget _scanningScreen() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const CircularProgressIndicator(),
            const SizedBox(height: 24),
            Text(_status, textAlign: TextAlign.center),
            const SizedBox(height: 24),
            TextButton(
              onPressed: () {
                // Abandon this scan: the generation guard drops its
                // late completion instead of switching screens.
                _opGen++;
                setState(() => _stage = Stage.home);
              },
              child: const Text('Back'),
            ),
            _errorBanner(),
          ],
        ),
      ),
    );
  }

  Widget _inventoryScreen() {
    final inv = _beforeInventory!;
    final zones = inv['zones'] as List;
    final counts = inv['category_counts'] as Map<String, dynamic>?;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        if (_overlayBytes != null)
          ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: Image.memory(_overlayBytes!),
          ),
        const SizedBox(height: 12),
        Text(
          '${inv['total_items']} items found',
          style: const TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
          textAlign: TextAlign.center,
        ),
        if (counts != null) ...[
          const SizedBox(height: 8),
          Text(
            counts.entries.map((e) => '${e.key} ${e.value}').join(' · '),
            textAlign: TextAlign.center,
          ),
        ],
        const SizedBox(height: 16),
        const Text('Quest zones:',
            style: TextStyle(fontWeight: FontWeight.bold)),
        for (final z in zones)
          ListTile(
            title: Text(z['label'] as String),
            trailing: Text('${z['item_count']} items'),
          ),
        if (_privacyReport != null) ...[
          const SizedBox(height: 8),
          Text(
            'Privacy: ${_privacyReport!['total_boxes_blurred']} region(s) '
            'blurred · face detector '
            '${_privacyReport!['face_detector_available'] == true ? 'on' : 'off'}'
            '${_labelBackend.isNotEmpty ? ' · labels: $_labelBackend' : ''}',
            textAlign: TextAlign.center,
          ),
        ],
        const SizedBox(height: 16),
        FilledButton(
          onPressed: () {
            setState(() => _stage = Stage.energy);
            _persist();
          },
          child: const Text('Build my quest →'),
        ),
        _privacyNote(),
        _errorBanner(),
      ],
    );
  }

  Widget _energyScreen() {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const Text(
          'How much energy do you actually have?',
          style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 8),
        const Text(
          'Your quest will fit inside this — never more.',
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 24),
        for (final entry in energyLabels.entries)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: FilledButton(
              onPressed: () => _buildPlan(entry.key),
              child: Text(entry.value),
            ),
          ),
        _errorBanner(),
      ],
    );
  }

  Widget _questScreen() {
    final plan = _plan!;
    final quests = plan['quests'] as List;
    final current = _restored ? _firstOpenStep() : null;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        if (_demoMode)
          const Chip(label: Text('Demo quest — pre-cached')),
        if (current != null) ...[
          Text(
            'Welcome back — your current task: ${current['instruction']}',
            style: const TextStyle(fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 12),
        ],
        Text(
          plan['quest_title'] as String? ?? 'Your quest',
          style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
        ),
        if (plan['intro_line'] != null) ...[
          const SizedBox(height: 4),
          Text(plan['intro_line'] as String),
        ],
        const SizedBox(height: 4),
        Text('${plan['est_minutes']} minutes · ${energyLabels[plan['energy_level']] ?? plan['energy_level']}'),
        const SizedBox(height: 16),
        for (final q in quests) ...[
          Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          q['title'] as String,
                          style: const TextStyle(fontWeight: FontWeight.bold),
                        ),
                      ),
                      if (q['is_boss'] == true)
                        const Chip(
                          label: Text('BOSS BATTLE'),
                          backgroundColor: Colors.amber,
                        ),
                    ],
                  ),
                  for (final s in q['steps'] as List)
                    _stepTile(s as Map<String, dynamic>),
                ],
              ),
            ),
          ),
          const SizedBox(height: 8),
        ],
        if (_skipped.isNotEmpty) ...[
          FilledButton(
            onPressed: _reroute,
            child: Text('Re-plan around ${_skipped.length} skipped step(s)'),
          ),
          const SizedBox(height: 8),
        ],
        if (_demoMode) ...[
          FilledButton(
            onPressed: () {
              setState(() => _stage = Stage.differ);
              _persist();
            },
            child: const Text('See the proof →'),
          ),
          const SizedBox(height: 8),
          FilledButton(
            onPressed: () {
              setState(() => _stage = Stage.card);
              _persist();
            },
            child: const Text('See the share card →'),
          ),
        ] else ...[
          FilledButton(
            onPressed: () {
              setState(() => _stage = Stage.rescan);
              _persist();
            },
            child: const Text('All done — take the after photo →'),
          ),
        ],
        _errorBanner(),
      ],
    );
  }

  Widget _stepTile(Map<String, dynamic> step) {
    final id = step['id'] as String;
    final skipped = _skipped.contains(id);
    final done = _done.contains(id);
    return Row(
      children: [
        Checkbox(
          value: done,
          onChanged: skipped
              ? null
              : (v) {
                    setState(() {
                      _restored = false;
                      if (v == true) {
                        _done.add(id);
                      } else {
                        _done.remove(id);
                      }
                    });
                    _persist();
                  },
        ),
        Expanded(
          child: Text(
            step['instruction'] as String,
            style: TextStyle(
              decoration: skipped
                  ? TextDecoration.lineThrough
                  : (done ? TextDecoration.lineThrough : null),
              color: skipped ? Colors.grey : null,
            ),
          ),
        ),
        if (step['is_mystery_box'] == true)
          const Tooltip(
            message: 'Mystery Box: open it, sort what you find',
            child: Icon(Icons.help_outline, size: 18),
          ),
        TextButton(
          onPressed: done
              ? null
              : () {
                    setState(() {
                      _restored = false;
                      if (skipped) {
                        _skipped.remove(id);
                      } else {
                        _skipped.add(id);
                      }
                    });
                    _persist();
                  },
          child: Text(skipped ? 'Unskip' : 'Skip'),
        ),
      ],
    );
  }

  Widget _rescanScreen() {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const Text(
          'Take the after photo from the same spot if you can.',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 16),
        CheckboxListTile(
          title: const Text('Different angle than the before photo'),
          value: _angleMismatch,
          onChanged: (v) {
            setState(() => _angleMismatch = v ?? false);
            _persist();
          },
        ),
        const SizedBox(height: 16),
        FilledButton.icon(
          onPressed: () => _pickAndScan(ImageSource.camera, after: true),
          icon: const Icon(Icons.camera_alt),
          label: const Text('Snap the after photo'),
        ),
        const SizedBox(height: 8),
        FilledButton.icon(
          onPressed: () => _pickAndScan(ImageSource.gallery, after: true),
          icon: const Icon(Icons.photo_library),
          label: const Text('Pick the after photo'),
        ),
        _errorBanner(),
      ],
    );
  }

  Widget _differScreen() {
    final d = _differ!;
    final totals = d['totals'] as Map<String, dynamic>;
    final zones = d['zones'] as Map<String, dynamic>;
    final notes = d['notes'] as List?;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const Text(
          'Progress, proven by re-scan',
          style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 12),
        Text(
          '${totals['before_total']} → ${totals['after_total']} items',
          style: const TextStyle(fontSize: 28, fontWeight: FontWeight.bold),
          textAlign: TextAlign.center,
        ),
        Text(
          'Cleared ${totals['cleared']} (${totals['cleared_pct']}%)',
          textAlign: TextAlign.center,
        ),
        if (d['angle_mismatch'] == true) ...[
          const SizedBox(height: 8),
          const Text(
            'Different angles — zone totals are indicative, not exact.',
            textAlign: TextAlign.center,
            style: TextStyle(fontStyle: FontStyle.italic),
          ),
        ],
        if ((totals['cleared'] as num) < 0) ...[
          const SizedBox(height: 8),
          const Text(
            'The re-scan counted more than the first photo — different '
            'angles can do that. No reduction is claimed from this pair; '
            'your card will use the plan numbers instead.',
            textAlign: TextAlign.center,
            style: TextStyle(fontStyle: FontStyle.italic),
          ),
        ],
        if (notes != null)
          for (final n in notes)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(n as String,
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontStyle: FontStyle.italic)),
            ),
        const SizedBox(height: 16),
        const Text('Zones:', style: TextStyle(fontWeight: FontWeight.bold)),
        for (final entry in zones.entries)
          ListTile(
            title: Text(entry.key),
            trailing: Text(
              '${entry.value['before_count']} → ${entry.value['after_count']}',
            ),
          ),
        const SizedBox(height: 16),
        if (_demoMode)
          FilledButton(
            onPressed: () => setState(() => _stage = Stage.card),
            child: const Text('See the share card →'),
          )
        else
          FilledButton(
            onPressed: _makeCard,
            child: const Text('Make my share card →'),
          ),
        _errorBanner(),
      ],
    );
  }

  Widget _cardScreen() {
    final sidecar = _sidecar;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        if (_cardBytes != null)
          ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: Image.memory(_cardBytes!),
          ),
        const SizedBox(height: 12),
        if (sidecar != null) ...[
          Text(
            sidecar['stats_text'] as String? ?? '',
            style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 8),
        ],
        if (!_demoMode) ...[
          FilledButton.icon(
            onPressed: _shareCard,
            icon: const Icon(Icons.share),
            label: const Text('Share'),
          ),
          const SizedBox(height: 8),
        ],
        FilledButton(
          onPressed: _reset,
          child: const Text('Start a new room'),
        ),
        _privacyNote(),
        _errorBanner(),
      ],
    );
  }
}
