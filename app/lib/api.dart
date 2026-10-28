import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

/// Thin client for the TidyQuest backend proxy (see app-server/proxy.py).
/// The proxy runs the local stage 1-3 pipeline on luci. It never calls
/// the Meta Model API: the Spark/Image integration points stay
/// coded-but-not-called until free credits are confirmed.
///
/// BYOK: [apiKey] is the user's own Meta Model API key, entered in the
/// app's settings field (ships EMPTY by default). When set, it is sent
/// as the X-Meta-API-Key header, per request, from the app layer only.
/// It is never baked into the app, never written to disk by this
/// client, and never appears in any response the proxy returns.
class TidyQuestApi {
  TidyQuestApi(this.baseUrl, {this.apiKey = ''});

  String baseUrl;

  /// The user's own Meta Model API key. Empty by default (BYOK).
  String apiKey;

  Map<String, String> get _headers {
    final headers = {'Content-Type': 'application/json'};
    final key = apiKey.trim();
    if (key.isNotEmpty) {
      headers['X-Meta-API-Key'] = key;
    }
    return headers;
  }

  Uri _uri(String path) {
    final base = baseUrl.endsWith('/')
        ? baseUrl.substring(0, baseUrl.length - 1)
        : baseUrl;
    return Uri.parse('$base$path');
  }

  Future<Map<String, dynamic>> _post(
      String path, Map<String, dynamic> body) async {
    final resp = await http.post(
      _uri(path),
      headers: _headers,
      body: jsonEncode(body),
    );
    final decoded = jsonDecode(resp.body) as Map<String, dynamic>;
    if (resp.statusCode != 200) {
      throw Exception(decoded['error'] ?? 'proxy error ${resp.statusCode}');
    }
    return decoded;
  }

  Future<Map<String, dynamic>> _get(String path) async {
    final resp = await http.get(_uri(path), headers: _headers);
    final decoded = jsonDecode(resp.body) as Map<String, dynamic>;
    if (resp.statusCode != 200) {
      throw Exception(decoded['error'] ?? 'proxy error ${resp.statusCode}');
    }
    return decoded;
  }

  Future<void> checkHealth() => _get('/health');

  /// Photo -> inventory + privacy-processed overlay image bytes.
  Future<Map<String, dynamic>> inventory(
      Uint8List bytes, String filename) {
    return _post('/inventory', {
      'image_b64': base64Encode(bytes),
      'filename': filename,
    });
  }

  Future<Map<String, dynamic>> plan(
      Map<String, dynamic> inventory, String energyLevel,
      {String spaceType = 'room'}) {
    return _post('/plan', {
      'inventory': inventory,
      'energy_level': energyLevel,
      'space_type': spaceType,
    });
  }

  Future<Map<String, dynamic>> reroute(
      Map<String, dynamic> plan, List<String> skippedStepIds) {
    return _post('/reroute', {
      'plan': plan,
      'skipped_step_ids': skippedStepIds,
    });
  }

  Future<Map<String, dynamic>> differ(
    Map<String, dynamic> beforeInventory,
    Map<String, dynamic> afterInventory, {
    bool angleMismatch = false,
    String spaceType = 'room',
  }) {
    return _post('/differ', {
      'before_inventory': beforeInventory,
      'after_inventory': afterInventory,
      'angle_mismatch': angleMismatch,
      'space_type': spaceType,
    });
  }

  Future<Map<String, dynamic>> card({
    required Uint8List beforeBytes,
    required String beforeName,
    required Uint8List afterBytes,
    required String afterName,
    required Map<String, dynamic> plan,
    Map<String, dynamic>? differ,
    String? beforeImageId,
    String spaceType = 'room',
  }) {
    return _post('/card', {
      'before_b64': base64Encode(beforeBytes),
      'before_name': beforeName,
      'after_b64': base64Encode(afterBytes),
      'after_name': afterName,
      'plan': plan,
      'differ': differ,
      'before_image_id': beforeImageId,
      'space_type': spaceType,
    });
  }

  /// Pre-cached demo flow: plan + differ + share card.
  Future<Map<String, dynamic>> demo(String which) => _get('/demo/$which');
}
