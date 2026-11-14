import 'dart:async';
import 'dart:js_interop';

import 'package:flutter/material.dart';
import 'package:web/web.dart' as web;

import '../../core/api_client.dart';
import '../../core/api_provider.dart';
import '../../core/app_theme.dart';
import '../../shared/models/models.dart';

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
    final web.Blob blob = web.Blob(
      <JSAny>[file.bytes.toJS].toJS,
      web.BlobPropertyBag(type: file.mimeType),
    );
    final String url = web.URL.createObjectURL(blob);
    final web.HTMLAnchorElement anchor = web.HTMLAnchorElement()
      ..href = url
      ..download = file.filename
      ..style.display = 'none';
    web.document.body?.appendChild(anchor);
    anchor.click();
    anchor.remove();
    web.URL.revokeObjectURL(url);
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
