import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/api_client.dart';
import '../../core/api_provider.dart';
import '../../core/app_theme.dart';
import '../../shared/models/models.dart';
import 'download_file_stub.dart'
    if (dart.library.js_interop) 'download_file_web.dart';

/// Кнопка скачивания результата анализа в PDF или GeoJSON.
///
/// Вызывает `POST /api/v1/runs/{runId}/exports` через [ApiClient] и показывает
/// ссылку на готовый артефакт. Код 410 Gone означает, что артефакт удалён по
/// политике хранения.
class ExportButton extends StatefulWidget {
  const ExportButton({
    required this.runId,
    required this.format,
    super.key,
  });

  /// Идентификатор запуска.
  final String runId;

  /// Формат экспорта.
  final ExportFormat format;

  @override
  State<ExportButton> createState() => _ExportButtonState();
}

class _ExportButtonState extends State<ExportButton> {
  bool _loading = false;

  String get _label =>
      widget.format == ExportFormat.pdf ? 'Скачать PDF' : 'Скачать GeoJSON';

  Future<void> _export() async {
    setState(() {
      _loading = true;
    });
    final ApiClient client = ApiScope.of(context);
    final ScaffoldMessengerState messenger = ScaffoldMessenger.of(context);
    try {
      final Artifact artifact =
          await client.createExport(widget.runId, widget.format);
      final DownloadedFile file =
          await client.downloadArtifact(artifact.contentUrl);
      if (!mounted) {
        return;
      }
      _triggerDownload(file);
      messenger.showSnackBar(
        SnackBar(content: Text('Файл скачан: ${file.filename}')),
      );
    } on ApiException catch (error) {
      if (!mounted) {
        return;
      }
      final String message = error.isGone
          ? 'Артефакт удалён по политике хранения'
          : error.message;
      messenger.showSnackBar(SnackBar(content: Text(message)));
    } finally {
      if (mounted) {
        setState(() {
          _loading = false;
        });
      }
    }
  }

  void _triggerDownload(DownloadedFile file) {
    triggerBrowserDownload(file);
  }

  @override
  Widget build(BuildContext context) {
    return OutlinedButton.icon(
      onPressed: _loading
          ? null
          : () {
              unawaited(_export());
            },
      icon: _loading
          ? const SizedBox(
              width: 16,
              height: 16,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : const Icon(Icons.download, size: 18),
      label: Text(_label),
      style: OutlinedButton.styleFrom(
        minimumSize: const Size.fromHeight(46),
        foregroundColor: AppColors.text,
        side: const BorderSide(color: AppColors.border),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
      ),
    );
  }
}
