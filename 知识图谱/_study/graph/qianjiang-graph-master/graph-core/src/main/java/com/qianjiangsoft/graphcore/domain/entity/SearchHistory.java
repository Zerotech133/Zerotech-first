package com.qianjiangsoft.graphcore.domain.entity;

import com.baomidou.mybatisplus.annotation.TableId;
import lombok.Data;

import java.util.Date;

@Data
public class SearchHistory {
    @TableId
    private String id;

    private String type;

    private String param;

    private Date createTime;

    private String name;

    private String spaceId;
}