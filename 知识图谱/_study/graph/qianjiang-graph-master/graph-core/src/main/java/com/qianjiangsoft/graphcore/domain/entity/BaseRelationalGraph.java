package com.qianjiangsoft.graphcore.domain.entity;

import com.baomidou.mybatisplus.annotation.TableId;
import lombok.Data;

import java.util.Date;

@Data
public class BaseRelationalGraph {

    @TableId
    private String id;

    private String libraryId;

    private String name;

    private String spaceId;

    private String imageUrl;

    private String data;

    private Date createTime;

    private String remark;
}