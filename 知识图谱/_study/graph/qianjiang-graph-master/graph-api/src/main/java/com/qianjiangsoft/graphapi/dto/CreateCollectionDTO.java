package com.qianjiangsoft.graphapi.dto;

import io.swagger.models.auth.In;
import lombok.Data;

import java.util.List;

@Data
public class CreateCollectionDTO {

    private Integer id;

    private String name;

    private List<String> vertexIdList;
}