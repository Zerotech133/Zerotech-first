package com.qianjiangsoft.graphcore.domain.entity;

import lombok.Data;

@Data
public class SchemaTemplate {
    private Integer id;

    private String name;

    private String edgeTypeList;

    private String tagList;
}