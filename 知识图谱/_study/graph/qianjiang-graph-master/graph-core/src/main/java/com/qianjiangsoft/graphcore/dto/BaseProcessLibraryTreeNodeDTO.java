package com.qianjiangsoft.graphcore.dto;

import lombok.Data;

@Data
public class BaseProcessLibraryTreeNodeDTO {

    private String id;

    private String name;

    private String parentId;

    private Long tableCount;

    private String type;
}
