import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:tidyquest/main.dart';

void main() {
  testWidgets('TidyQuest home screen renders', (tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(const TidyQuestApp());
    expect(find.text('TidyQuest'), findsWidgets);
    expect(find.text('Snap the messy room'), findsOneWidget);
  });

  testWidgets('Restored session lands back on the quest', (tester) async {
    SharedPreferences.setMockInitialValues({
      'tidyquest_session_v1': jsonEncode({
        'version': 1,
        'stage': 'quests',
        'beforeName': 'photo.jpg',
        'beforeB64': null,
        'beforeInventory': {'image_id': 'test'},
        'overlayB64': null,
        'privacyReport': null,
        'labelBackend': '',
        'plan': {
          'quest_title': "Donna's Bathroom Reclaim",
          'intro_line': 'Test intro',
          'est_minutes': 18,
          'energy_level': 'an-hour',
          'quests': [
            {
              'title': 'Bathroom',
              'is_boss': false,
              'steps': [
                {
                  'id': 's1',
                  'instruction': 'Toss the trash',
                  'is_mystery_box': false,
                },
              ],
            },
          ],
        },
        'done': [],
        'skipped': [],
        'afterName': 'after.jpg',
        'afterB64': null,
        'afterInventory': null,
        'differ': null,
        'cardB64': null,
        'sidecar': null,
        'angleMismatch': false,
        'demoMode': false,
      }),
    });
    await tester.pumpWidget(const TidyQuestApp());
    await tester.pumpAndSettle();
    expect(find.text("Donna's Bathroom Reclaim"), findsOneWidget);
    expect(find.text('Toss the trash'), findsOneWidget);
    expect(find.textContaining('Welcome back'), findsOneWidget);
  });
}
