import 'change_enums.dart';
import 'json_utils.dart';

/// Артефакт запуска (схема Artifact из contracts/api.yaml).
class Artifact {
  const Artifact({
    required this.id,
    required this.runId,
    required this.kind,
    required this.contentUrl,
    this.sizeBytes = 0,
    this.sha256,
    this.isHeavy = false,
    this.retentionUntil,
    this.purgeNoticeAt,
    this.isDeleted = false,
    this.createdAt,
  });

  /// Идентификатор артефакта.
  final String id;

  /// Идентификатор запуска.
  final String runId;

  /// Вид артефакта.
  final ArtifactKind kind;

  /// Ссылка на скачивание содержимого.
  final String contentUrl;

  /// Размер файла в байтах.
  final int sizeBytes;

  /// SHA-256 файла.
  final String? sha256;

  /// Признак тяжёлого файла (больше 100 МБ, FR-022).
  final bool isHeavy;

  /// Срок хранения (`null` — бессрочно).
  final DateTime? retentionUntil;

  /// Время уведомления о предстоящей очистке.
  final DateTime? purgeNoticeAt;

  /// Признак удалённого артефакта.
  final bool isDeleted;

  /// Время создания артефакта.
  final DateTime? createdAt;

  /// Создаёт модель из JSON-объекта контракта.
  factory Artifact.fromJson(Map<String, dynamic> json) {
    return Artifact(
      id: JsonUtils.asStringOr(json['id'], ''),
      runId: JsonUtils.asStringOr(json['runId'], ''),
      kind: ArtifactKind.fromWire(json['kind']),
      contentUrl: JsonUtils.asStringOr(json['contentUrl'], ''),
      sizeBytes: JsonUtils.asIntOr(json['sizeBytes'], 0),
      sha256: JsonUtils.asString(json['sha256']),
      isHeavy: JsonUtils.asBoolOr(json['isHeavy'], false),
      retentionUntil: JsonUtils.asDateTime(json['retentionUntil']),
      purgeNoticeAt: JsonUtils.asDateTime(json['purgeNoticeAt']),
      isDeleted: JsonUtils.asBoolOr(json['isDeleted'], false),
      createdAt: JsonUtils.asDateTime(json['createdAt']),
    );
  }
}
