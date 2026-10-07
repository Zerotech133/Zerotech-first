package com.qianjiangsoft.graphapi.dto;

import lombok.Data;

import java.util.List;

@Data
public class CollectionListDTO {
    private Integer id;

    private String name;

    private List<String> vertexIdList;
}