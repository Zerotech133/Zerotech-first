package com.qianjiangsoft.graphcore.domain.entity;

import lombok.Data;

@Data
public class ExampleEdge {
    private String type;

    private String srcId;

    private String dstId;

    private String attributes;
}