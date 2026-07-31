from ppt_agent.workers.ppt_verifier import _compact_mapped_slides_for_review


def test_compact_review_uses_mapped_zones_and_deduplicates_layers():
    slides = [
        {
            "slide_index": 3,
            "zones": [
                {
                    "type": "title",
                    "action": "replace_text",
                    "content": "智能行程规划",
                },
                {
                    "type": "title",
                    "action": "replace_text",
                    "content": "智能行程规划",
                },
                {
                    "type": "body",
                    "action": "replace_text",
                    "content": "结合偏好与实时路况生成路线",
                },
            ],
        }
    ]

    result = _compact_mapped_slides_for_review(slides)

    assert result[0]["title"] == "智能行程规划"
    assert [item["text"] for item in result[0]["key_copy"]] == [
        "智能行程规划",
        "结合偏好与实时路况生成路线",
    ]


def test_compact_review_omits_decorative_fragments_and_preserved_labels():
    slides = [
        {
            "zones": [
                {"type": "title", "action": "replace_text", "content": "行"},
                {
                    "type": "decorative",
                    "action": "replace_text",
                    "content": "报告",
                },
                {
                    "type": "footer",
                    "action": "preserve",
                    "content": "模板页脚",
                },
                {
                    "type": "body",
                    "action": "replace_text",
                    "content": "离线与安全容灾",
                },
            ]
        }
    ]

    result = _compact_mapped_slides_for_review(slides)

    assert result[0]["title"] == "离线与安全容灾"
    assert result[0]["key_copy"] == [
        {"role": "body", "text": "离线与安全容灾"}
    ]
