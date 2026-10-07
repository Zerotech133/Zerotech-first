package com.qianjiangsoft.graphcore.domain.entity;

import java.util.Date;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;

@Data
public class GraphAnalyseTask {

    @TableId(type = IdType.AUTO)
    private Integer id;

    private String name;

    private String type;

    private Integer status;

    private Date createTime;

    private Date finishTime;

    private String spaceId;

    private String param;

    @TableLogic
    private Boolean delFlag;
}