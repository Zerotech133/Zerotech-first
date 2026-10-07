package com.qianjiangsoft.graphapi.service.impl;

import cn.hutool.core.bean.BeanUtil;
import com.github.pagehelper.PageInfo;
import com.qianjiangsoft.graphapi.dto.SpaceInfoListDTO;
import com.qianjiangsoft.graphapi.param.CreateSpaceDTO;
import com.qianjiangsoft.graphapi.service.SpaceInfoService;
import com.qianjiangsoft.graphcore.constants.ResultCode;
import com.qianjiangsoft.graphcore.domain.entity.GraphSpace;
import com.qianjiangsoft.graphcore.exception.BusinessException;
import com.qianjiangsoft.graphcore.mapper.GraphSpaceMapper;
import com.qianjiangsoft.graphcore.page.PageDataVO;
import com.qianjiangsoft.graphcore.page.PageUtil;
import com.qianjiangsoft.graphcore.service.SpacePersistenceService;
import com.qianjiangsoft.graphcore.service.SpaceService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/12/1 12:21
 */
@Service
public class SpaceInfoServiceImpl implements SpaceInfoService {

    @Autowired
    SpaceService spaceService;

    @Autowired
    GraphSpaceMapper graphSpaceMapper;

    @Autowired
    SpacePersistenceService spacePersistenceService;

    @Autowired
    private ThreadPoolTaskExecutor commonThreadPoolTaskExecutor;



    @Override
    public CreateSpaceDTO createSpace(String id, String name) {
        return BeanUtil.copyProperties(spaceService.createSpace(id, name), CreateSpaceDTO.class);
    }

    @Override
    public void deleteSpace(String id) {
        spaceService.deleteSpace(id);
    }

    @Override
    public PageDataVO<SpaceInfoListDTO> selectSpaceListByPagePage() {
        PageUtil.startPage("create_time desc");
        List<GraphSpace> spaceList = spaceService.selectSpaceList(null);
        return PageUtil.getPageDataVO(BeanUtil.copyToList(spaceList, SpaceInfoListDTO.class), PageInfo.of(spaceList).getTotal());
    }

    @Override
    public void checkSpaceStatus(String spaceId) {

        GraphSpace graphSpace = graphSpaceMapper.selectById(spaceId);

        if(graphSpace == null) {
            throw new BusinessException(ResultCode.DATA_NOT_EXISTS, "空间不存在，请刷新图库");
        }

        synchronized (this) {

            //未激活状态需要加载数据
            if(graphSpace.getActiveFlag() == 0) {

                //触发恢复数据
                //加载数据
                spacePersistenceService.loadSpace(graphSpace);

                //更新状态
                graphSpace.setActiveFlag(1);
                graphSpaceMapper.updateById(graphSpace);
            }
        }
    }
}
