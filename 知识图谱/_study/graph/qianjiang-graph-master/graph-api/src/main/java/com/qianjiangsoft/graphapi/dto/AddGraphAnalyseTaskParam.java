package com.qianjiangsoft.graphapi.dto;

import lombok.Data;

@Data
public class AddGraphAnalyseTaskParam {

    private Object name;

    private String type;

    private String spaceId;

    private String param;
}