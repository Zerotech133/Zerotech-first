package com.qianjiangsoft.graphcore.domain.entity;

import com.baomidou.mybatisplus.annotation.TableId;
import lombok.Data;

import java.util.Date;

@Data
public class GraphSpaceData {

    @TableId
    private String spaceId;

    private Date updateTime;

    private String data;
}