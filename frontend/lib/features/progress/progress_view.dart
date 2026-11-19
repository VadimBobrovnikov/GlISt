import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/api_client.dart';
import '../../core/api_provider.dart';
import '../../core/app_theme.dart';
import '../../shared/models/models.dart';
import '../result/result_view.dart';

/// Экран прогресса анализа: 4 карточки агентов и опрос статуса запуска.
///
/// Пока запуск в статусах «ожидает» / «в обработке», экран опрашивает
/// `GET /api/v1/runs/{runId}`. По завершении открывает [ResultScreen].
class ProgressScreen extends StatefulWidget {
  const ProgressScreen({
    required this.runId,
    required this.queryText,
    this.analysisType,
    super.key,
  });

  /// Идентификатор запуска.
  final String runId;

  /// Текст исходного запроса для отображения.
  final String queryText;

  /// Сценарий запуска: определяет подпись шага анализа.
  final AnalysisType? analysisType;

  @override
  State<ProgressScreen> createState() => _ProgressScreenState();
}

class _ProgressScreenState extends State<ProgressScreen> {
  ApiClient? _client;
  Run? _run;
  String? _error;
  double _progress = 0.05;
  bool _finished = false;
  Timer? _pollTimer;
  Timer? _animTimer;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _client ??= ApiScope.of(context);
    if (_pollTimer == null) {
      _pollTimer = Timer.periodic(const Duration(seconds: 2), (Timer timer) {
        unawaited(_poll());
      });
      _animTimer = Timer.periodic(const Duration(milliseconds: 450), (
        Timer timer,
      ) {
        if (!mounted || _finished) {
          return;
        }
        setState(() {
          if (_progress < 0.9) {
            _progress += 0.03;
          }
        });
      });
      unawaited(_poll());
    }
  }

  @override
  void dispose() {
    _stopTimers();
    super.dispose();
  }

  void _stopTimers() {
    _pollTimer?.cancel();
    _animTimer?.cancel();
    _pollTimer = null;
    _animTimer = null;
  }

  Future<void> _poll() async {
    final ApiClient? client = _client;
    if (client == null || _finished) {
      return;
    }
    try {
      final Run run = await client.getRun(widget.runId);
      if (!mounted) {
        return;
      }
      setState(() {
        _run = run;
      });
      if (run.status == RunStatus.completed ||
          run.status == RunStatus.validationFailed) {
        await _openResult();
      } else if (run.status == RunStatus.error ||
          run.status == RunStatus.noData) {
        _stopTimers();
        setState(() {
          _error = run.statusReason ?? 'Не удалось выполнить анализ';
        });
      }
    } on ApiException catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _error = error.message;
      });
    }
  }

  Future<void> _openResult() async {
    if (_finished) {
      return;
    }
    _finished = true;
    _stopTimers();
    if (mounted) {
      setState(() {
        _progress = 1;
      });
    }
    await Future<void>.delayed(const Duration(milliseconds: 500));
    if (!mounted) {
      return;
    }
    final NavigatorState navigator = Navigator.of(context);
    await navigator.pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) => ResultScreen(runId: widget.runId),
      ),
    );
  }

  void _cancel() {
    _stopTimers();
    Navigator.of(context).pop();
  }

  int get _activeIndex {
    final int total = _agentsFor(widget.analysisType).length;
    final Run? run = _run;
    if (run != null &&
        (run.status == RunStatus.completed ||
            run.status == RunStatus.validationFailed)) {
      return total;
    }
    final int raw = (_progress * total).floor();
    return raw.clamp(0, total - 1).toInt();
  }

  _StageState _stageState(int index) {
    final Run? run = _run;
    if (run != null &&
        (run.status == RunStatus.completed ||
            run.status == RunStatus.validationFailed)) {
      return _StageState.completed;
    }
    if (run != null &&
        (run.status == RunStatus.error || run.status == RunStatus.noData)) {
      return index <= _activeIndex ? _StageState.failed : _StageState.waiting;
    }
    if (index < _activeIndex) {
      return _StageState.completed;
    }
    if (index == _activeIndex) {
      return _StageState.running;
    }
    return _StageState.waiting;
  }

  String _stageLog(int index, _StageState state) {
    switch (state) {
      case _StageState.completed:
        return 'Этап завершён';
      case _StageState.running:
        return 'Агент обрабатывает данные…';
      case _StageState.failed:
        return 'Этап прерван';
      case _StageState.waiting:
        return 'Ожидание предыдущего агента';
    }
  }

  @override
  Widget build(BuildContext context) {
    final TextTheme textTheme = Theme.of(context).textTheme;
    final bool failed = _error != null;
    return Scaffold(
      appBar: AppBar(title: const Text('Анализ территории')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 1100),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  _buildQueryCard(),
                  const SizedBox(height: 24),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: <Widget>[
                      Text(
                        'Анализ территории',
                        style: textTheme.titleMedium?.copyWith(
                          fontWeight: FontWeight.w700,
                          color: AppColors.text,
                        ),
                      ),
                      Text(
                        '${(_progress * 100).round()}%',
                        style: const TextStyle(
                          color: AppColors.primary,
                          fontWeight: FontWeight.w700,
                          fontSize: 20,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(6),
                    child: LinearProgressIndicator(
                      value: _progress,
                      minHeight: 8,
                      backgroundColor: AppColors.border,
                      color: AppColors.primary,
                    ),
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Агенты последовательно обрабатывают данные',
                    style: TextStyle(color: AppColors.muted, fontSize: 13),
                  ),
                  const SizedBox(height: 20),
                  if (failed) _buildError() else _buildAgents(),
                  const SizedBox(height: 28),
                  Center(
                    child: TextButton(
                      onPressed: _cancel,
                      child: const Text('Отменить и вернуться к запросу'),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildQueryCard() {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppColors.border),
      ),
      child: Row(
        children: <Widget>[
          const Icon(Icons.auto_awesome, color: AppColors.primary),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              widget.queryText,
              style: const TextStyle(fontSize: 16, color: AppColors.text),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildAgents() {
    final List<_AgentSpec> agents = _agentsFor(widget.analysisType);
    return LayoutBuilder(
      builder: (BuildContext context, BoxConstraints constraints) {
        final bool narrow = constraints.maxWidth < 760;
        final double cardWidth = narrow
            ? constraints.maxWidth
            : (constraints.maxWidth - 48) / 4;
        return Wrap(
          spacing: 16,
          runSpacing: 16,
          children: List<Widget>.generate(agents.length, (int index) {
            final _StageState state = _stageState(index);
            final double stageProgress = state == _StageState.completed
                ? 1
                : state == _StageState.running
                    ? 0.6
                    : 0;
            return SizedBox(
              width: cardWidth,
              child: _AgentCard(
                spec: agents[index],
                state: state,
                logText: _stageLog(index, state),
                progress: stageProgress,
              ),
            );
          }),
        );
      },
    );
  }

  Widget _buildError() {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: AppColors.errorBg,
        borderRadius: BorderRadius.circular(16),
      ),
      child: Row(
        children: <Widget>[
          const Icon(Icons.error_outline, color: AppColors.errorText),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              _error ?? 'Не удалось выполнить анализ',
              style: const TextStyle(color: AppColors.errorText),
            ),
          ),
        ],
      ),
    );
  }
}

enum _StageState { waiting, running, completed, failed }

class _AgentSpec {
  const _AgentSpec({
    required this.icon,
    required this.name,
    required this.role,
  });

  final IconData icon;
  final String name;
  final String role;
}

const List<_AgentSpec> _agents = <_AgentSpec>[
  _AgentSpec(
    icon: Icons.manage_search,
    name: 'Researcher',
    role: 'Поиск данных',
  ),
  _AgentSpec(
    icon: Icons.insights,
    name: 'Analyst',
    role: 'Анализ спутниковых данных',
  ),
  _AgentSpec(
    icon: Icons.layers_outlined,
    name: 'Visualizer',
    role: 'Построение карты',
  ),
  _AgentSpec(
    icon: Icons.check_circle_outline,
    name: 'Validator',
    role: 'Проверка результата',
  ),
];

/// Подпись шага анализа по сценарию: шаг не должен называться NDVI в
/// запуске про застройку или затопление.
String _analystRole(AnalysisType? analysisType) {
  switch (analysisType) {
    case AnalysisType.flood:
      return 'Анализ воды (MNDWI)';
    case AnalysisType.builtUp:
      return 'Анализ застройки (NDBI)';
    case AnalysisType.vegetation:
    case null:
      return 'Анализ NDVI';
  }
}

List<_AgentSpec> _agentsFor(AnalysisType? analysisType) {
  return <_AgentSpec>[
    for (final _AgentSpec spec in _agents)
      spec.name == 'Analyst'
          ? _AgentSpec(
              icon: spec.icon,
              name: spec.name,
              role: _analystRole(analysisType),
            )
          : spec,
  ];
}

class _AgentCard extends StatelessWidget {
  const _AgentCard({
    required this.spec,
    required this.state,
    required this.logText,
    required this.progress,
  });

  final _AgentSpec spec;
  final _StageState state;
  final String logText;
  final double progress;

  Color get _badgeBackground {
    switch (state) {
      case _StageState.waiting:
        return const Color(0xFFF1F5F9);
      case _StageState.running:
        return const Color(0xFFDBEAFE);
      case _StageState.completed:
        return AppColors.successBg;
      case _StageState.failed:
        return AppColors.errorBg;
    }
  }

  Color get _badgeForeground {
    switch (state) {
      case _StageState.waiting:
        return AppColors.muted;
      case _StageState.running:
        return AppColors.primary;
      case _StageState.completed:
        return AppColors.successText;
      case _StageState.failed:
        return AppColors.errorText;
    }
  }

  Color get _progressColor {
    switch (state) {
      case _StageState.waiting:
        return AppColors.border;
      case _StageState.running:
        return AppColors.primary;
      case _StageState.completed:
        return AppColors.retained;
      case _StageState.failed:
        return AppColors.lost;
    }
  }

  String get _badgeLabel {
    switch (state) {
      case _StageState.waiting:
        return 'Ожидает';
      case _StageState.running:
        return 'В процессе';
      case _StageState.completed:
        return 'Завершено';
      case _StageState.failed:
        return 'Ошибка';
    }
  }

  @override
  Widget build(BuildContext context) {
    final bool highlighted = state == _StageState.running;
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: highlighted ? AppColors.primary : AppColors.border,
          width: highlighted ? 1.5 : 1,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(
                  color: highlighted ? AppColors.accentSoft : const Color(0xFFF1F5F9),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Icon(
                  spec.icon,
                  color: highlighted ? AppColors.primary : AppColors.muted,
                  size: 20,
                ),
              ),
              const Spacer(),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                  color: _badgeBackground,
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Text(
                  _badgeLabel,
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                    color: _badgeForeground,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          Text(
            spec.name,
            style: const TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w700,
              color: AppColors.text,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            spec.role,
            style: const TextStyle(fontSize: 13, color: AppColors.muted),
          ),
          const SizedBox(height: 16),
          Text(
            logText,
            style: const TextStyle(fontSize: 12, color: AppColors.muted),
          ),
          const SizedBox(height: 12),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: progress,
              minHeight: 6,
              backgroundColor: AppColors.border,
              color: _progressColor,
            ),
          ),
        ],
      ),
    );
  }
}
