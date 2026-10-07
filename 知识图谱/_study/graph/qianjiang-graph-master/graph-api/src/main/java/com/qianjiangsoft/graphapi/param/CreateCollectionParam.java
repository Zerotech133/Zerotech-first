package com.qianjiangsoft.graphapi.param;

import lombok.Data;

import java.util.List;

@Data
public class CreateCollectionParam {

    private String name;

    private List<String> vertexIdList;
}