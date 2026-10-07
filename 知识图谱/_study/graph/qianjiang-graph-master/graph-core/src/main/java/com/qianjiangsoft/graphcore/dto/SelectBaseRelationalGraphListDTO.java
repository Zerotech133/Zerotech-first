package com.qianjiangsoft.graphcore.dto;

import lombok.Data;

import java.util.Date;

@Data
public class SelectBaseRelationalGraphListDTO {

    private String id;

    private String libraryId;

    private String name;

    private String caseId;

    private String imageUrl;

    private Date createTime;

    private String createBy;

    private String remark;

    private String suspicionId;

    private String relationType;

    private String strength;

    private String relationLibraryId;//关联分析的批次
    private String flag;
}