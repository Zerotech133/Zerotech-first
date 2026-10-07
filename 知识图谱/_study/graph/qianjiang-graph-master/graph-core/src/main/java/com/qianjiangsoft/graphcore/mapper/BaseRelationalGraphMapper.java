package com.qianjiangsoft.graphcore.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.qianjiangsoft.graphcore.domain.entity.BaseRelationalGraph;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface BaseRelationalGraphMapper extends BaseMapper<BaseRelationalGraph> {

}